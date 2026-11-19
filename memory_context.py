"""Explicit local memory lookup and defaults; residence is not live geolocation."""
import re


def residence(memories):
    values = {m['name']: m['value'] for m in memories}
    for name in ('居住地', '常住城市', '居住城市'):
        if values.get(name):
            return {'value': values[name], 'memory_key': name, 'source': 'user_saved_memory'}
    return None


def local_memory_answer(query, memories):
    text = re.sub(r'[\s？?。！!]', '', query)
    home_questions = {'居住地在哪', '居住地在哪儿', '居住地在哪里', '居住地在那儿',
                      '我的居住地', '我的居住地在哪', '我的居住地在哪儿', '我的居住地在哪里',
                      '我住在哪里', '我住在哪', '我住在哪儿', '我住哪', '我在哪里', '我在哪',
                      '我在哪儿', '你记得我住在哪里吗'}
    if text in home_questions:
        home = residence(memories)
        if home:
            answer = f"你保存的{home['memory_key']}是：{home['value']}。"
        else:
            answer = '尚未保存居住地。可以用 /remember 居住地=城市名称 设置。'
        if text in {'我在哪里', '我在哪', '我在哪儿'}:
            answer += '\n这是已保存的居住信息；我没有获取你的实时位置。'
        return answer
    return None


def defaults(query, memories):
    home = residence(memories)
    if home and any(word in query for word in ('天气', '气温', '下雨', '降水')):
        return {'weather_city': home, 'policy': '仅当当前请求和补充都未指定地点时使用；明确地点优先。居住地不是实时位置。'}
    return {}
