# Erisia Skill - execute_skill v10
# Improved from v9: autonomous re-forge routed to improvement
# Location before improvement: pending

import requests

TOOL_SCHEMA = {
    "name": "check_weather_mumbai",
    "description": "Retrieves and displays the current weather in Mumbai using the weatherapi.com API.",
    "parameters": {
        "api_key": {"type": "str", "required": True},
        "city": {"type": "str", "default": "Mumbai"},
        "units": {"type": "str", "default": "metric"}
    },
    "returns": "A descriptive string containing the current weather conditions"
}

def execute_skill(**kwargs):
    """
    Retrieves and displays the current weather in Mumbai using the weatherapi.com API.

    Args:
        **kwargs: Keyword arguments
            - api_key (str): The API key from weatherapi.com
            - city (str): The city name (default: Mumbai)
            - units (str): The unit system (default: metric)

    Returns:
        str: A descriptive string containing the current weather conditions
    """
    api_key = kwargs.get('api_key')
    city = kwargs.get('city', 'Mumbai')
    units = kwargs.get('units', 'metric')

    try:
        response = requests.get(f'http://api.weatherapi.com/v1/current.json?key={api_key}&q={city}')
        response.raise_for_status()
    except requests.exceptions.HTTPError as errh:
        return f"HTTP Error: {errh}"
    except requests.exceptions.ConnectionError as errc:
        return f"Error Connecting: {errc}"
    except requests.exceptions.Timeout as errt:
        return f"Timeout Error: {errt}"
    except requests.exceptions.RequestException as err:
        return f"Something went wrong: {err}"

    data = response.json()

    try:
        current_weather = data['current']
        condition = current_weather['condition']
        temperature = current_weather['temp_c'] if units == 'metric' else current_weather['temp_f']
        humidity = current_weather['humidity']
        wind_speed = current_weather['wind_kph'] if units == 'metric' else current_weather['wind_mph']

        return f"Weather in {city}: {condition['text']}, Temperature: {temperature}°{units[:1].upper()}, Humidity: {humidity}%, Wind Speed: {wind_speed} {units[:3].upper()}"
    except KeyError as e:
        return f"Invalid response: {e}"

# Example usage
api_key = "YOUR_API_KEY_HERE"
print(execute_skill(api_key=api_key))