import importlib.util,sys,tempfile,pathlib,contextlib,io
p=str(pathlib.Path(__file__).with_name('sync_dev_urls.py'))
spec=importlib.util.spec_from_file_location('sync_urls',p);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
 root=pathlib.Path(d);m.WORKSPACE=root
 for key in ['API_LOCAL','MCP_ENV','API_ENV','ROUTER_LOCAL','SMART_ENV']:
  f=root/key;f.write_text('UNCHANGED=fixture\n');setattr(m,key,f)
 initial={k:getattr(m,k).read_text() for k in ['MCP_ENV','API_ENV','ROUTER_LOCAL','SMART_ENV']}
 m.configure_stores('https://uninstall-resale-bring.ngrok-free.dev')
 for k,v in initial.items():assert getattr(m,k).read_text()==v
 once=m.API_LOCAL.read_text();m.configure_stores('https://uninstall-resale-bring.ngrok-free.dev');assert m.API_LOCAL.read_text()==once
 m.apply(m.Urls(mcp='https://mcp.example.com',api='https://api.example.com'))
 assert m.env_get(m.API_LOCAL,'INTEGRATIONS_PUBLIC_BASE_URL')=='https://uninstall-resale-bring.ngrok-free.dev'
 m.apply(m.Urls(mcp=m.LOCAL_MCP,api=m.LOCAL_API))
 assert m.env_get(m.API_LOCAL,'INTEGRATIONS_PUBLIC_BASE_URL')=='https://uninstall-resale-bring.ngrok-free.dev'
 for invalid in ['http://foo.com','https://foo.com/path','https://user:pass@foo.com','https://foo.com?x=1']:
  try:m.configure_stores(invalid)
  except SystemExit:pass
  else:raise AssertionError(invalid)
print('PASS: isolated update, idempotence, fixed origin preserved in public/local modes, invalid origins rejected')
