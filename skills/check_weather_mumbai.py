# Erisia Skill - check_weather_mumbai v2
# Improved from v1: merged with similar skill 'check_weather'
# Location before improvement: pending

import requests
import json

def execute_skill(**kwargs):
    api_key = "YOUR_API_KEY"
    base_url = "http://api.openweathermap.org/data/2.5/weather"
    city = "Mumbai"
    params = {
        "q": city,
        "appid": api_key,
        "units": "metric"
    }
    response = requests.get(base_url, params=params)
    weather_data = response.json()
    return weather_data
TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_weather",
        "description": "Check the weather in Mumbai",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
}