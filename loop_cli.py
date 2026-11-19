"""CLI for persisted, multi-turn Jev execution."""
import json
import os
import uuid
from contextlib import ExitStack
from pathlib import Path
from agent_core import DeepSeekResponder, ToolError, jev_router_from_env
from memory_store import MemoryStore
from loop_agent import Generator, LoopAgent
from tool_registry import ToolRegistry
from terminal_view import HELP, print_answer, tool_title


def run_loop(args, parser):
    from mcp_client import MCPManager
    from app import show_tools
    registry = ToolRegistry(include_builtins=False)
    config = args.mcp_config or str(Path(__file__).with_name('mcp.loop.json'))
    with ExitStack() as stack:
        stack.enter_context(MCPManager(config, registry))
        if args.list_tools:
            show_tools(registry)
            return
        if args.tool:
            arguments = json.loads(args.arguments)
            if not isinstance(arguments, dict):
                raise ValueError('--args 必须为 JSON 对象')
            print(json.dumps(registry.execute(args.tool, arguments), ensure_ascii=False, indent=2))
            return
        if args.mode != 'jev':
            parser.error('多步骤模式需要 --mode jev；旧离线演示使用 --engine legacy --mode offline')
        if not os.getenv('DEEPSEEK_API_KEY'):
            parser.error('多步骤模式需要 DEEPSEEK_API_KEY，用于参数提取和回答；Jev 负责决策')
        router = jev_router_from_env(min_confidence=args.confidence)
        store = MemoryStore(args.memory_db)
        stack.callback(store.close)
        agent = LoopAgent(router, Generator(DeepSeekResponder(os.environ['DEEPSEEK_API_KEY'])),
                          registry, store, args.user, args.session, args.max_steps, args.trace)
        def execute(query):
            shown = False
            def progress(message):
                nonlocal shown
                if not shown:
                    print('\n执行步骤\n' + '─' * 54)
                    shown = True
                for name in registry.specs:
                    message = message.replace(name, tool_title(name))
                message = message.replace('tool_call', '调用工具').replace('finish / none', '生成回答').replace('request_info', '需要补充').replace('review', '待复核')
                print('  ' + message)
            out = agent.run(query, progress=progress)
            print_answer(out)
        if args.query:
            execute(args.query)
            return
        if args.demo:
            for query in ['北京明天天气怎么样，给我穿衣建议', '那上海呢？比较一下', '只用一句话总结刚才的比较']:
                print('\n你> ' + query)
                execute(query)
            return
        print('\n' + '═' * 54)
        print('Jev 多轮工具助手')
        print(f'用户：{agent.user}    会话：{agent.session}    工具：{len(registry.specs)} 个')
        print('═' * 54)
        print('\n直接输入需求即可。Jev 决定步骤，DeepSeek 整理回答。')
        print('会话自动保存；天气为联网预报，/result 显示历史快照。')
        print('\n常用命令')
        print('  /help      全部命令与示例')
        print('  /result    查看已有结果，无需联网')
        print('  /new       开始新话题，保留偏好')
        print('  /retry     重试未完成任务')
        print('  /memory    查看长期记忆')
        print('  /exit      保存并退出')
        if store.load(agent.user, agent.session).get('task'):
            print('\n已恢复未完成任务：回复“继续”恢复；查看 /result；切换话题用 /new。')
        while True:
            try:
                query = input('\n你> ').strip()
                if query.startswith('/'):
                    query = '/' + query[1:].lstrip()
            except (EOFError, KeyboardInterrupt):
                break
            if query in {'exit', 'quit', '/exit'}:
                break
            try:
                if not query:
                    continue
                if query == '/help':
                    print('\n' + HELP + '\n')
                elif query == '/tools':
                    print('\n可用工具\n')
                    for name, spec in registry.specs.items():
                        print(f'  {tool_title(name)} — {spec.description}')
                    print('\n参数详情：/tools details')
                elif query == '/tools details':
                    show_tools(registry)
                elif query == '/memory':
                    memories = store.memories(agent.user)
                    print('\n长期记忆\n')
                    if not memories:
                        print('  尚未保存。用 /remember 名称=内容 添加。')
                    for memory in memories:
                        print(f"  {memory['name']}：{memory['value']}")
                    print()
                elif query.startswith('/remember '):
                    name, separator, value = query[len('/remember '):].partition('=')
                    if not separator:
                        raise ValueError('用法：/remember 名称=内容')
                    store.remember(agent.user, name, value)
                    print(f'已保存：{name.strip()} = {value.strip()}')
                    if name.strip() in {'居住地', '常住城市', '居住城市'}:
                        print('未指定城市的天气查询将以此为默认地点；明确指定的城市优先。')
                elif query.startswith('/forget '):
                    store.forget(agent.user, query[len('/forget '):].strip())
                    print('已删除指定记忆。')
                elif query == '/new':
                    agent.session = uuid.uuid4().hex[:12]
                    print('新会话：' + agent.session + '；保留用户偏好。用 --session 此ID 可恢复。')
                elif query == '/reset':
                    store.reset(agent.user, agent.session)
                    print('当前会话和待执行任务已清空；长期记忆保留。')
                elif query == '/retry':
                    state = store.load(agent.user, agent.session)
                    if state.get('task'):
                        # Explicit user request permits another attempt, retaining observations.
                        state['task']['attempts'] = []
                        store.save(agent.user, agent.session, state)
                        execute('重试尚未完成的任务；优先处理失败步骤。')
                    else:
                        print('没有待重试任务。')
                elif query.startswith('/session '):
                    name = query[len('/session '):].strip()
                    if not name or len(name) > 100:
                        raise ValueError('会话ID需为1–100字符')
                    agent.session = name
                    print('已切换会话：' + name)
                elif query in {'/result', '/result details'}:
                    execute(query)
                elif query.startswith('/'):
                    print('未知命令。输入 /help 查看命令说明。')
                else:
                    execute(query)
            except (ValueError, ToolError) as exc:
                print('操作失败：' + str(exc))
