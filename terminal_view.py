"""Plain terminal presentation; no model/network calls."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

TOOLS = {
    'search_location': '查询城市位置', 'weather_forecast': '查询天气预报',
    'list_directory': '列出目录', 'read_text': '读取文件', 'search_files': '搜索文件',
    'text_stats': '统计文本', 'current_time': '查询当前时间',
    'calculate': '计算表达式', 'date_offset': '计算日期',
}
STATUSES = {'quota_exceeded': '模型额度不足', 'completed': '已完成', 'partial_result': '已有结果', 'request_info': '需要补充信息',
            'review_needed': '已暂停', 'system_error': '服务连接或响应异常',
            'step_limit': '达到执行上限', 'cannot_answer': '暂时无法完成'}
HELP = '''常用命令
  /help                  查看这份帮助
  /result                查看已有结果，不联网、不调用模型
  /result details        查看原始结果、参数、完整来源和错误
  /tools                 查看工具用途
  /tools details         查看工具名称及参数 Schema

会话与恢复
  /retry                 重试未完成任务（可能产生 API 费用）
  /new                   开启新会话，保留长期记忆
  /session ID            切换或恢复指定会话
  /reset                 清空当前会话及待办任务，保留长期记忆

长期记忆
  /memory                查看已保存的偏好
  /remember 名称=内容    保存或更新一条长期记忆
  /forget 名称           删除一条长期记忆

退出
  /exit 或 exit          退出；会话自动保存在本地

示例：/remember 出行偏好=喜欢博物馆，优先公共交通
提示：任务未完成时，下一句话会作为补充；切换话题请先 /new。'''


def tool_title(name):
    for suffix, title in TOOLS.items():
        if name.endswith('_' + suffix):
            return title
    return name or '工具'


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
            ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M（北京时间）')
    except (ValueError, TypeError, AttributeError):
        return '未知'


def error_summary(error):
    text = str(error)
    if 'Tunnel connection failed' in text:
        return '代理连接失败，未取得数据'
    if 'SSL' in text or 'EOF occurred' in text:
        return '安全连接中断，未取得数据'
    if 'timeout' in text.lower() or '超时' in text:
        return '请求超时，未取得数据'
    return text[:180]


def results(task, details=False):
    observations = task.get('observations', [])
    if not observations:
        return '暂无工具查询结果。可以直接输入需求开始查询。'
    if details:
        return json.dumps(task, ensure_ascii=False, indent=2)
    locations = {}
    def payload(o):
        output = o.get('output')
        value = output.get('result', {}) if isinstance(output, dict) else {}
        return value if isinstance(value, dict) else {}
    for o in observations:
        for place in payload(o).get('locations', []):
            locations[(place.get('latitude'), place.get('longitude'))] = place.get('name')
    successes, failures, intermediates, seen, truncated = [], [], [], set(), False
    for o in reversed(observations):
        if o.get('truncated') and 'tool' not in o:
            truncated = True
            continue
        args = o.get('arguments') or {}
        name = o.get('tool', '')
        signature = json.dumps([name, args], ensure_ascii=False, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        data = payload(o)
        label = locations.get((args.get('latitude'), args.get('longitude')),
                              f"位置（{args.get('latitude')}, {args.get('longitude')}）")
        if o.get('error'):
            subject = f"{label} · {args.get('start_date', '')}" if name.endswith('_weather_forecast') else tool_title(name)
            failures.append(f"  {subject}\n    {error_summary(o['error'])}")
            continue
        if name.endswith('_search_location'):
            intermediates.append(args.get('name', '城市'))
            continue
        daily = data.get('daily')
        if isinstance(daily, dict):
            def val(key, i):
                values = daily.get(key) or []
                return values[i] if i < len(values) and values[i] is not None else '未知'
            units = data.get('units') or {}
            for i, day in enumerate(daily.get('time') or []):
                successes.append(f"  {label} · {day}（逐日预报）\n"
                                 f"    气温：{val('temperature_2m_min', i)} ～ {val('temperature_2m_max', i)} {units.get('temperature_2m_max', '')}\n"
                                 f"    最大降水概率：{val('precipitation_probability_max', i)} {units.get('precipitation_probability_max', '')}\n"
                                 f"    最大风速：{val('wind_speed_10m_max', i)} {units.get('wind_speed_10m_max', '')}\n"
                                 f"    来源：Open-Meteo｜查询时间：{timestamp(data.get('retrieved_at'))}")
        elif 'value' in data:
            successes.append(f"  计算结果：{data['value']}")
        elif 'text' in data:
            successes.append(f"  文件：{data.get('path', args.get('path', '未知'))}\n    " +
                             str(data['text'])[:1500].replace('\n', '\n    ') +
                             ('\n    [内容有截断，使用 /result details 查看保存的内容]' if data.get('truncated') or len(str(data['text'])) > 1500 else ''))
        else:
            text = json.dumps(data, ensure_ascii=False)
            successes.append(f"  {tool_title(name)}\n    {text[:1200]}" + ('\n    [展示已截断]' if len(text) > 1200 else ''))
    lines = ['已保存的查询结果', '以下是历史查询快照，不会重新联网；请以具体日期和查询时间为准。']
    if task.get('request'):
        lines += ['', '任务：' + str(task['request'])[:160]]
    if successes:
        lines += ['', '查询成功', '\n\n'.join(successes)]
    if failures:
        lines += ['', '尚未取得的数据', '\n\n'.join(failures), '', '信息不完整，暂不能据此给出完整比较。']
    if not successes and intermediates:
        lines += ['', '已完成地点检索：' + '、'.join(dict.fromkeys(intermediates)), '地点候选不等于天气结果，天气还需继续查询。']
    if truncated:
        lines += ['', '部分旧版历史记录已截断，无法完整恢复；需要时请重新查询。']
    lines += ['', '查看原始参数、地点候选、完整来源及错误：/result details']
    return '\n'.join(lines)


def print_answer(out):
    print('\n' + '─' * 54)
    print('回答｜' + STATUSES.get(out['status'], out['status']))
    if out.get('latency_ms'):
        print(f"耗时：{out['latency_ms'] / 1000:.1f} 秒")
    print('\n' + out['answer'] + '\n')
    print('─' * 54)
