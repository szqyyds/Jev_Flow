"""Real Open-Meteo queries; fixed provider URLs, no simulated fallback."""
import json
import math
import sys
from pathlib import Path
from datetime import date, datetime, timezone as dt_timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from network_io import read_json

server = FastMCP('Weather', log_level='WARNING')


def fetch(base, params):
    url = base + '?' + urlencode(params)
    result = read_json(Request(url, headers={'User-Agent': 'JevAgentDemo/2.0'}),
                       timeout=12, max_bytes=500000)
    if not isinstance(result, dict):
        raise ValueError('天气服务响应必须为对象')
    if result.get('error'):
        raise ValueError(result.get('reason', '天气服务失败'))
    return result, url


@server.tool()
def search_location(name: str, country_code: str = '') -> dict[str, Any]:
    """联网搜索城市的经纬度和时区。重名地点需结合国家/行政区确认；可使用城市英文名重试。"""
    if not 1 <= len(name.strip()) <= 100:
        raise ValueError('城市名需为 1–100 字符')
    params = {'name': name, 'count': 5, 'language': 'zh', 'format': 'json'}
    if country_code:
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValueError('国家代码需为两位字母')
        params['countryCode'] = country_code.upper()
    data, url = fetch('https://geocoding-api.open-meteo.com/v1/search', params)
    fields = ('name', 'latitude', 'longitude', 'timezone', 'country', 'admin1')
    return {'locations': [{k: r.get(k) for k in fields} for r in data.get('results', [])],
            'source': 'OPEN_METEO_LIVE', 'url': url, 'retrieved_at': datetime.now(dt_timezone.utc).isoformat()}


@server.tool()
def weather_forecast(latitude: float, longitude: float, start_date: str, end_date: str,
                     timezone: str = 'Asia/Shanghai') -> dict[str, Any]:
    """按已查询的经纬度和 YYYY-MM-DD 日期获取真实逐日预报；不是历史天气，超出预测范围会报错。"""
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError('经纬度不合法')
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if not 0 <= (end - start).days <= 15:
        raise ValueError('查询范围必须为 1–16 天')
    data, url = fetch('https://api.open-meteo.com/v1/forecast', {
        'latitude': latitude, 'longitude': longitude, 'start_date': start_date, 'end_date': end_date,
        'timezone': timezone, 'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max'})
    return {'daily': data.get('daily'), 'units': data.get('daily_units'), 'timezone': data.get('timezone'),
            'source': 'OPEN_METEO_LIVE', 'url': url, 'retrieved_at': datetime.now(dt_timezone.utc).isoformat()}


if __name__ == '__main__':
    server.run(transport='stdio')
