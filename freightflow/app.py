"""FreightFlow: dependency-free freight operations reference service."""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS shipments (
 id TEXT PRIMARY KEY, customer TEXT NOT NULL, origin TEXT NOT NULL,
 destination TEXT NOT NULL, pieces INTEGER NOT NULL CHECK(pieces > 0),
 status TEXT NOT NULL CHECK(status IN ('CREATED','DISPATCHED','IN_TRANSIT','DELIVERED','CANCELLED')),
 version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id TEXT NOT NULL,
 type TEXT NOT NULL, payload TEXT NOT NULL, occurred_at TEXT NOT NULL,
 FOREIGN KEY(shipment_id) REFERENCES shipments(id)
);
CREATE TABLE IF NOT EXISTS idempotency (
 key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, shipment_id TEXT NOT NULL
);
'''
TRANSITIONS = {'CREATED': {'DISPATCHED','CANCELLED'}, 'DISPATCHED': {'IN_TRANSIT','CANCELLED'},
               'IN_TRANSIT': {'DELIVERED'}, 'DELIVERED': set(), 'CANCELLED': set()}

def now():
    return datetime.now(timezone.utc).isoformat()

class DomainError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status

class FreightStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def get(self, shipment_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM shipments WHERE id=?', (shipment_id,)).fetchone()
            if not row:
                raise DomainError('NOT_FOUND','Shipment not found',404)
            return dict(row)

    def list(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM shipments ORDER BY created_at DESC, id DESC')]

    def history(self, shipment_id):
        self.get(shipment_id)
        with self.lock:
            return [dict(r) | {'payload':json.loads(r['payload'])} for r in self.db.execute(
                'SELECT * FROM events WHERE shipment_id=? ORDER BY id', (shipment_id,))]

    def create(self, data, idem_key=None):
        if not isinstance(data, dict):
            raise DomainError('INVALID_REQUEST','JSON object required')
        for field in ('customer','origin','destination'):
            if not isinstance(data.get(field),str) or not data[field].strip():
                raise DomainError('VALIDATION_ERROR',f'{field} is required')
        pieces = data.get('pieces')
        if type(pieces) is not int or not 1 <= pieces <= 100000:
            raise DomainError('VALIDATION_ERROR','pieces must be an integer between 1 and 100000')
        if data['origin'].strip().casefold() == data['destination'].strip().casefold():
            raise DomainError('VALIDATION_ERROR','origin and destination must differ')
        normalized = {k:data[k].strip() for k in ('customer','origin','destination')} | {'pieces':pieces}
        canonical = json.dumps(normalized,sort_keys=True)
        import hashlib
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.lock, self.db:
            if idem_key:
                existing = self.db.execute('SELECT * FROM idempotency WHERE key=?',(idem_key,)).fetchone()
                if existing:
                    if existing['request_hash'] != digest:
                        raise DomainError('IDEMPOTENCY_CONFLICT','Key already used for different request',409)
                    return self.get(existing['shipment_id']), False
            shipment_id = str(uuid.uuid4())
            timestamp = now()
            self.db.execute('INSERT INTO shipments VALUES (?,?,?,?,?,?,?,?,?)',
                (shipment_id,normalized['customer'],normalized['origin'],normalized['destination'],pieces,'CREATED',0,timestamp,timestamp))
            self._event(shipment_id,'SHIPMENT_CREATED',normalized,timestamp)
            if idem_key:
                self.db.execute('INSERT INTO idempotency VALUES (?,?,?)',(idem_key,digest,shipment_id))
            return self.get(shipment_id), True

    def _event(self, shipment_id, event_type, payload, timestamp):
        self.db.execute('INSERT INTO events (shipment_id,type,payload,occurred_at) VALUES (?,?,?,?)',
                        (shipment_id,event_type,json.dumps(payload,sort_keys=True),timestamp))

    def transition(self, shipment_id, status, expected_version=None):
        with self.lock, self.db:
            current = self.get(shipment_id)
            if expected_version is not None and (type(expected_version) is not int or expected_version != current['version']):
                raise DomainError('VERSION_CONFLICT','Shipment was updated; refresh and retry',409)
            if status not in TRANSITIONS[current['status']]:
                raise DomainError('INVALID_TRANSITION',f"Cannot transition from {current['status']} to {status}",409)
            timestamp = now()
            self.db.execute('UPDATE shipments SET status=?, version=version+1, updated_at=? WHERE id=?',
                            (status,timestamp,shipment_id))
            self._event(shipment_id,'SHIPMENT_'+status,{'from':current['status'],'to':status},timestamp)
            return self.get(shipment_id)

class Handler(BaseHTTPRequestHandler):
    store = None
    dashboard = None
    def log_message(self, fmt, *args):
        pass
    def respond(self, status, data):
        raw=json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(raw)
    def request_json(self):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length > 65536 or length < 1:
                raise ValueError()
            return json.loads(self.rfile.read(length))
        except (ValueError,json.JSONDecodeError):
            raise DomainError('INVALID_JSON','Request body must be valid JSON under 64 KiB')
    def route(self, method):
        path=urlparse(self.path).path
        if path == '/' and method=='GET':
            raw=self.dashboard
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path in ('/app.js','/static.css') and method=='GET':
            name=path[1:]
            raw=(Path(__file__).parent/name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/javascript' if name.endswith('.js') else 'text/css')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path=='/health' and method=='GET':
            return self.respond(200,{'status':'ok'})
        if path=='/api/shipments':
            if method=='GET': return self.respond(200,{'shipments':self.store.list()})
            if method=='POST':
                record, created=self.store.create(self.request_json(),self.headers.get('Idempotency-Key'))
                return self.respond(201 if created else 200,record)
        parts=path.strip('/').split('/')
        if len(parts)>=3 and parts[:2]==['api','shipments']:
            shipment_id=parts[2]
            if len(parts)==3 and method=='GET': return self.respond(200,self.store.get(shipment_id))
            if len(parts)==4 and parts[3]=='events' and method=='GET':
                return self.respond(200,{'events':self.store.history(shipment_id)})
            if len(parts)==4 and parts[3]=='status' and method=='POST':
                payload=self.request_json()
                if not isinstance(payload,dict): raise DomainError('INVALID_REQUEST','JSON object required')
                return self.respond(200,self.store.transition(shipment_id,payload.get('status'),payload.get('expected_version')))
        raise DomainError('NOT_FOUND','Endpoint not found',404)
    def do_GET(self): self._handle('GET')
    def do_POST(self): self._handle('POST')
    def _handle(self,method):
        try: self.route(method)
        except DomainError as err: self.respond(err.status,{'error':err.code,'message':str(err)})
        except Exception:
            self.respond(500,{'error':'INTERNAL_ERROR','message':'Unexpected server error'})

def server(store,host='127.0.0.1',port=8080):
    class BoundHandler(Handler): pass
    BoundHandler.store=store
    BoundHandler.dashboard=(Path(__file__).parent/'index.html').read_bytes()
    return ThreadingHTTPServer((host,port),BoundHandler)

if __name__=='__main__':
    db_path=os.environ.get('FREIGHTFLOW_DB','freightflow.db')
    httpd=server(FreightStore(db_path),os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8080')))
    print(f'FreightFlow running at http://{httpd.server_address[0]}:{httpd.server_address[1]}',flush=True)
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally: httpd.server_close()
