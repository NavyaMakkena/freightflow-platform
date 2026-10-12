import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from freightflow.app import FreightStore, server

class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.store=FreightStore(Path(cls.temp.name)/'test.db')
        cls.http=server(cls.store,port=0)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.http.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.thread.join();cls.http.server_close();cls.store.close();cls.temp.cleanup()
    def request(self,path,method='GET',data=None,headers=None):
        body=json.dumps(data).encode() if data is not None else None
        request=Request(self.base+path,method=method,data=body,headers=({'Content-Type':'application/json'} | (headers or {})))
        try:
            with urlopen(request,timeout=5) as r:
                return r.status,json.loads(r.read())
        except HTTPError as e:
            return e.code,json.loads(e.read())
    def create(self,**overrides):
        payload={'customer':'Acme','origin':'Fort Worth','destination':'Denver','pieces':12}|overrides
        return self.request('/api/shipments','POST',payload)
    def test_health_and_dashboard(self):
        status,body=self.request('/health');self.assertEqual((status,body),(200,{'status':'ok'}))
        for path,expected in [('/','FreightFlow'),('/app.js','refresh'),('/static.css','.panel')]:
            with urlopen(self.base+path) as r:
                self.assertEqual(r.status,200);self.assertIn(expected,r.read().decode())
    def test_complete_delivery_lifecycle(self):
        status,shipment=self.create();self.assertEqual(status,201)
        sid=shipment['id'];self.assertEqual(shipment['status'],'CREATED')
        for version,target in enumerate(('DISPATCHED','IN_TRANSIT','DELIVERED')):
            status,shipment=self.request(f'/api/shipments/{sid}/status','POST',{'status':target,'expected_version':version})
            self.assertEqual(status,200);self.assertEqual(shipment['status'],target)
            self.assertEqual(shipment['version'],version+1)
        status,history=self.request(f'/api/shipments/{sid}/events')
        self.assertEqual(status,200)
        self.assertEqual([e['type'] for e in history['events']],['SHIPMENT_CREATED','SHIPMENT_DISPATCHED','SHIPMENT_IN_TRANSIT','SHIPMENT_DELIVERED'])
        self.assertEqual(self.request(f'/api/shipments/{sid}')[1]['status'],'DELIVERED')
        self.assertIn(sid,[s['id'] for s in self.request('/api/shipments')[1]['shipments']])
    def test_cancel_and_illegal_transition(self):
        _,shipment=self.create();sid=shipment['id']
        status,_=self.request(f'/api/shipments/{sid}/status','POST',{'status':'CANCELLED'})
        self.assertEqual(status,200)
        status,err=self.request(f'/api/shipments/{sid}/status','POST',{'status':'DELIVERED'})
        self.assertEqual((status,err['error']),(409,'INVALID_TRANSITION'))
    def test_optimistic_locking(self):
        _,shipment=self.create();sid=shipment['id']
        self.request(f'/api/shipments/{sid}/status','POST',{'status':'DISPATCHED','expected_version':0})
        status,err=self.request(f'/api/shipments/{sid}/status','POST',{'status':'IN_TRANSIT','expected_version':0})
        self.assertEqual((status,err['error']),(409,'VERSION_CONFLICT'))
    def test_idempotent_create(self):
        payload={'customer':'Repeat','origin':'Austin','destination':'Chicago','pieces':3}
        headers={'Idempotency-Key':'test-unique-key'}
        first,a=self.request('/api/shipments','POST',payload,headers)
        second,b=self.request('/api/shipments','POST',payload,headers)
        self.assertEqual((first,second),(201,200));self.assertEqual(a['id'],b['id'])
        status,err=self.request('/api/shipments','POST',payload|{'pieces':4},headers)
        self.assertEqual((status,err['error']),(409,'IDEMPOTENCY_CONFLICT'))
    def test_validation_and_not_found(self):
        for payload in [ {'customer':'','origin':'A','destination':'B','pieces':1},
                         {'customer':'A','origin':'A','destination':'A','pieces':1},
                         {'customer':'A','origin':'A','destination':'B','pieces':0},
                         {'customer':'A','origin':'A','destination':'B','pieces':True}]:
            status,err=self.request('/api/shipments','POST',payload)
            self.assertEqual((status,err['error']),(400,'VALIDATION_ERROR'))
        self.assertEqual(self.request('/api/shipments/does-not-exist')[0],404)
        self.assertEqual(self.request('/api/unknown')[0],404)
    def test_persistence_reopen(self):
        _,shipment=self.create(customer='Persistence')
        other=FreightStore(self.store.db_path)
        try:self.assertEqual(other.get(shipment['id'])['customer'],'Persistence')
        finally:other.close()

if __name__=='__main__':unittest.main()
