import py

from pypy.tool.build.webserver import Handler, Resource, Collection, \
                                      HTTPError

class NonInitHandler(Handler):
    request_version = '1.0'

    def __init__(self):
        pass

    def log_request(self, code='-', size='-'):
        pass

class SomePage(Resource):
    def handle(self, server, path, query):
        return ('text/plain', 'foo')

def build_app_structure():
    app = Collection()
    app.sub = Collection()
    app.sub = Collection()
    app.sub.index = SomePage()
    return app

class TestCollection(object):
    def test_traverse(self):
        app = build_app_structure()
        assert app.traverse(['sub', 'index'], '/sub/index') is app.sub.index
        assert app.traverse(['sub', ''], '/sub/') is app.sub.index
        try:
            app.traverse(['sub'], '/sub')
        except HTTPError, e:
            assert e.status == 301
            assert e.data == '/sub/'
        else:
            py.test.fail('should have redirected')
        # 404 errors (first -> no index)
        py.test.raises(HTTPError, "app.traverse([''], '/')")
        py.test.raises(HTTPError, "app.traverse(['other', ''], '/other/')")

class TestResource(object):
    pass

class TestHandler(object):
    def setup_method(self, method):
        self.handler = NonInitHandler()
        self.handler.wfile = self.wfile = py.std.StringIO.StringIO()

    def test_process_path(self):
        path, query = self.handler.process_path('')
        assert path == ''
        assert query == ''
    
        path, query = self.handler.process_path('/foo')
        assert path == '/foo'
        assert query == ''

        path, query = self.handler.process_path('/foo?bar')
        assert path == '/foo'
        assert query == 'bar'

        py.test.raises(ValueError, "self.handler.process_path('/foo?bar?baz')")

    def test_find_resource(self):
        app = build_app_structure()
        self.handler.application = app
        assert self.handler.find_resource('/sub/index', '') is app.sub.index
        assert self.handler.find_resource('/sub/', '') is app.sub.index
        try:
            self.handler.find_resource('/sub', '')
        except HTTPError, e:
            assert e.status == 301
            assert e.data == '/sub/'
        else:
            py.test.raises('should have raised a redirect')
        try:
            self.handler.find_resource('', '')
        except HTTPError, e:
            assert e.status == 301
            assert e.data == '/'
        else:
            py.test.raises('should have raised a redirect')
        py.test.raises(HTTPError, "self.handler.find_resource('/foo/', '')")

    def test_response(self):
        self.handler.response(200, {'Content-Type': 'text/plain'}, 'foo')
        response = self.wfile.getvalue()
        assert response.startswith('HTTP/1.0 200 OK')
        assert 'Content-Type: text/plain\r\n' in response
        assert 'Content-Length: 3\r\n' in response
        assert response.endswith('\r\n\r\nfoo')

    def test_get_response_file(self):
        rfile = py.std.StringIO.StringIO()
        rfile.write('foo\nbar\nbaz')
        rfile.seek(0)
        self.handler.response(200, {'Content-Type': 'text/plain'}, rfile)
        response = self.wfile.getvalue()
        assert response.endswith('\r\n\r\nfoo\nbar\nbaz')

    def test_get_response_wrong_body(self):
        py.test.raises(ValueError, "self.handler.response(200, {}, u'xxx')")

