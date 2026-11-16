import unittest
from unittest.mock import MagicMock, patch

from image_gen_workbench import __main__ as cli


class StartupTests(unittest.TestCase):
    def test_browser_opens_after_binding_and_uses_selected_port(self):
        events = []
        server = MagicMock()
        server.serve_forever.side_effect = lambda: events.append('serve')
        def bind(*args, **kwargs):
            events.append('bind')
            return server
        def start_thread(*, target, args, **kwargs):
            thread = MagicMock()
            thread.start.side_effect = lambda: target(*args)
            return thread
        with patch.object(cli, 'make_server', side_effect=bind) as factory, \
             patch.object(cli.threading, 'Thread', side_effect=start_thread), \
             patch.object(cli.webbrowser, 'open', side_effect=lambda url: events.append(url) or True):
            app = MagicMock()
            cli.run_server(app, 7862)
        factory.assert_called_once_with('127.0.0.1', 7862, app, threaded=True)
        self.assertEqual(events, ['bind', 'http://127.0.0.1:7862', 'serve'])
        server.server_close.assert_called_once()

    def test_no_browser_still_serves_and_closes_on_interrupt(self):
        server = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(cli, 'make_server', return_value=server), \
             patch.object(cli.threading, 'Thread') as thread:
            cli.run_server(MagicMock(), 7860, open_browser=False)
        thread.assert_not_called()
        server.server_close.assert_called_once()

    def test_bind_failure_does_not_open_browser(self):
        with patch.object(cli, 'make_server', side_effect=OSError('port occupied')), \
             patch.object(cli.threading, 'Thread') as thread:
            with self.assertRaises(OSError):
                cli.run_server(MagicMock(), 7860)
        thread.assert_not_called()

    def test_browser_failure_prints_manual_url(self):
        for failure in (False, RuntimeError('no browser')):
            with self.subTest(failure=failure), \
                 patch.object(cli.webbrowser, 'open') as browser, \
                 patch('sys.stderr') as stderr:
                if isinstance(failure, Exception):
                    browser.side_effect = failure
                else:
                    browser.return_value = failure
                cli.launch_browser('http://127.0.0.1:7860')
                self.assertIn('http://127.0.0.1:7860', ''.join(c.args[0] for c in stderr.write.call_args_list))
