# Erisia Skill - check_weather_mumbai v3
# Improved from v2: autonomous re-forge routed to improvement
# Location before improvement: active

import requests

TOOL_SCHEMA = {
    "api_key": {"type": "string", "required": True},
    "base_url": {"type": "string", "required": True},
    "city": {"type": "string", "required": False}
}

def check_weather_mumbai(**kwargs):
    api_key = kwargs.get("api_key")
    base_url = kwargs.get("base_url")
    city = kwargs.get("city", "Mumbai")

    try:
        response = requests.get(f"{base_url}?key={api_key}&q={city}")
        response.raise_for_status()
        data = response.json()
        current_weather = data['current']
        condition = current_weather['condition']
        return f"The current weather in {city} is {condition['text']} with a temperature of {current_weather['temp_c']}°C."
    except requests.exceptions.HTTPError as http_err:
        return f"HTTP error occurred: {http_err}"
    except requests.exceptions.ConnectionError as conn_err:
        return f"Error connecting to the API: {conn_err}"
    except requests.exceptions.Timeout as timeout_err:
        return f"Timeout error occurred: {timeout_err}"
    except requests.exceptions.RequestException as err:
        return f"Something went wrong: {err}"

def execute_skill(**kwargs):
    return check_weather_mumbai(**kwargs)

# Example usage
api_key = "YOUR_API_KEY"
base_url = "http://api.weatherapi.com/v1/current.json"
result = execute_skill(api_key=api_key, base_url=base_url)
print(result)