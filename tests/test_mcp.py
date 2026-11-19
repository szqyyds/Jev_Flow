"""Integration checks against real SDK stdio subprocesses, no model API keys."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from agent_core import Agent, JevRouter, OfflineRouter, Session, ToolError
from tool_registry import ToolRegistry

SDK_AVAILABLE = importlib.util.find_spec('mcp') is not None


@unittest.skipUnless(SDK_AVAILABLE, 'MCP 可选依赖未安装')
class MCPIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mcp_client import MCPManager
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.root = cls.folder / 'workspace'
        cls.root.mkdir()
        (cls.root / 'note.txt').write_text('hello Jev\n你好 MCP', encoding='utf-8')
        (cls.root / '.env').write_text('PRIVATE_FIXTURE=hidden', encoding='utf-8')
        (cls.folder / 'outside.txt').write_text('outside', encoding='utf-8')
        (cls.root / 'escape.txt').symlink_to(cls.folder / 'outside.txt')
        script = Path(__file__).resolve().parents[1] / 'mcp_servers' / 'local_tools.py'
        cls.config = cls.folder / 'mcp.json'
        cls.config.write_text(json.dumps({'mcpServers': {'local': {
            'command': sys.executable, 'args': [str(script), '--root', str(cls.root)],
            'read_only_tools': ['read_text', 'list_directory', 'search_files', 'text_stats', 'current_time'],
            'timeout': 3}}}), encoding='utf-8')
        cls.registry = ToolRegistry()
        cls.manager = MCPManager(str(cls.config), cls.registry)
        cls.manager.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.manager.__exit__(None, None, None)
        assert all(not client.thread.is_alive() for client in cls.manager.clients)
        cls.temp.cleanup()

    def test_discovery_and_registry_isolation(self):
        self.assertEqual(len(self.registry.specs), 11)
        self.assertEqual(len(ToolRegistry().specs), 6)
        spec = self.registry.specs['mcp_local_read_text']
        self.assertEqual(spec.required, ('path',))
        self.assertEqual(spec.input_schema['properties']['path']['type'], 'string')

    def test_read_file_and_multiturn_slot_filling(self):
        agent = Agent(OfflineRouter(), registry=self.registry)
        session = Session()
        first = agent.run('读取文件', session)
        self.assertEqual(first['status'], 'request_info')
        second = agent.run('note.txt', session)
        self.assertEqual(second['status'], 'completed')
        self.assertIn('hello Jev', second['answer'])
        self.assertEqual(second['events'][1]['provenance']['path'], 'user_explicit')

    def test_real_tools(self):
        agent = Agent(OfflineRouter(), registry=self.registry)
        for query, expected in [('列出文件', 'note.txt'), ('搜索文件内容 Jev', 'hello Jev'),
                                ('文本统计：你好 Agent', 'chinese_characters'), ('当前时间 UTC', 'datetime')]:
            with self.subTest(query=query):
                result = agent.run(query)
                self.assertEqual(result['status'], 'completed')
                self.assertIn(expected, result['answer'])
                self.assertEqual(result['observations'][0]['output']['source'], 'MCP')
        self.assertNotIn('.env', agent.run('列出文件')['answer'])
        self.assertNotIn('escape.txt', agent.run('列出文件')['answer'])

    def test_schema_invalid_and_unknown_arguments_are_blocked(self):
        for arguments in [{'path': 42}, {'path': 'note.txt', 'unexpected': True}, {}]:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolError):
                    self.registry.execute('mcp_local_read_text', arguments)

    def test_path_escape_and_sensitive_files_blocked_by_server(self):
        for path in ['../outside.txt', '.env', 'escape.txt', str(self.folder / 'outside.txt')]:
            with self.subTest(path=path):
                with self.assertRaises(ToolError):
                    self.registry.execute('mcp_local_read_text', {'path': path})

    def test_tool_error_preserves_session(self):
        with self.assertRaises(ToolError):
            self.registry.execute('mcp_local_read_text', {'path': 'missing.txt'})
        result = self.registry.execute('mcp_local_current_time', {'timezone': 'UTC'})
        self.assertIn('datetime', json.dumps(result))

    def test_jev_sees_and_executes_dynamic_tool(self):
        seen = []
        def wire(payload):
            seen.append(payload)
            return {'answers': {
                'action': {'choice': 'tool_call', 'confidence': .99},
                'tool': {'choice': 'mcp_local_read_text', 'confidence': .99}}}
        result = Agent(JevRouter('', transport=wire), registry=self.registry).run('读取 note.txt')
        self.assertEqual(result['status'], 'completed')
        self.assertIn('mcp_local_read_text', seen[0]['questions']['tool']['criteria'])
        self.assertEqual(seen[0]['state']['tools']['mcp_local_read_text']['required'], ['path'])
        self.assertIn('hello Jev', result['answer'])

    def test_generic_json_arguments(self):
        result = Agent(OfflineRouter(), registry=self.registry).run('mcp_local_read_text {"path":"note.txt"}')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['events'][1]['provenance']['path'], 'user_explicit_json')

    def test_config_unknown_tool_fails_and_cleans_up(self):
        from mcp_client import MCPManager
        config = json.loads(self.config.read_text())
        config['mcpServers']['local']['read_only_tools'] = ['not_a_tool']
        bad = self.folder / 'bad.json'
        bad.write_text(json.dumps(config))
        manager = MCPManager(str(bad), ToolRegistry())
        with self.assertRaises(ValueError):
            manager.__enter__()
        self.assertTrue(all(not c.thread.is_alive() for c in manager.clients))

    def test_malformed_json_does_not_execute_default_arguments(self):
        result = Agent(OfflineRouter(), registry=self.registry).run('mcp_local_list_directory {"path":')
        self.assertEqual(result['status'], 'tool_error')
        self.assertFalse(any(e['stage'] == 'tool_result' for e in result['events']))

    def test_large_file_output_is_bounded(self):
        (self.root / 'large.txt').write_text('x' * 15000)
        result = self.registry.execute('mcp_local_read_text', {'path': 'large.txt'})
        self.assertTrue(result['result']['truncated'])
        self.assertEqual(len(result['result']['text']), 10000)

    def test_timeout_and_shutdown(self):
        from mcp_client import StdioClient
        from mcp import StdioServerParameters
        slow = self.folder / 'slow_server.py'
        slow.write_text('''import time
from mcp.server.fastmcp import FastMCP
server = FastMCP("slow", log_level="WARNING")
@server.tool()
def slow_tool() -> str:
    time.sleep(2)
    return "done"
server.run(transport="stdio")
''')
        client = StdioClient(StdioServerParameters(command=sys.executable, args=[str(slow)]), timeout=1)
        try:
            with self.assertRaises(ToolError):
                client.call('slow_tool', {})
        finally:
            client.close()
        self.assertFalse(client.thread.is_alive())
