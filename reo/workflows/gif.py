import requests
import json
import random

from reo.config.config import urls

def get_gif(name:str, limit:int=10):
    base_url = urls.gif_api_base
    params = {
        "q": name,
        "api_key": urls.gif_api_key,
        "limit": limit
    }
    response = requests.get(base_url, params=params)
    if response.status_code != 200:
        return None
    
    data = response.json()    
    # Giphy uses 'data', not 'results'
    results = data.get('data', [])
    
    selected_gif = random.choice(results) if results else None
    if not selected_gif:
        return None
    
    # Giphy uses 'images', not 'media'
    return selected_gif.get('images', {}).get('original', {}).get('url')

## Given by kudoshu
