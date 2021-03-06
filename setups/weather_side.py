#!/usr/bin/env python3

import flipdot
import pyowm
import re
import requests
import time
import traceback

from lxml import html
from owm_apikey import API_KEY

def upperfirst(x):
    return x[0].upper() + x[1:]

def _prepare_status(status):
    replacements = {
        "Überwiegend": "überw.",
        "Leichte Regenschauer": "Leichte Regensch."
    }
    status = upperfirst(status)
    for orig, repl in replacements.items():
        status = status.replace(orig, repl)
    return status

client = flipdot.FlipdotClient("localhost")

# Get weather alerts
plz = 63571
page = requests.get("http://www.unwetterzentrale.de/uwz/getwarning_de.php?plz={plz}&uwz=UWZ-DE&lang=de".format(plz = plz))
page.encoding = 'UTF-8'
tree = html.fromstring(page.content)
divs = tree.xpath('//*[@id="content"]/div')
warnings = []
forewarnings = []
for div in divs:
    # Only process divs that contain warnings
    warning = div.xpath('div[1]/div[1]/div[1]')
    if not warning:
        continue
    match = re.match(r"Unwetterwarnung Stufe (?P<level>\w+) vor (?P<what>\w+)", warning[0].text_content())
    match_pre = re.match(r"Vorwarnung vor (?P<what>\w+), Warnstufe (?P<level>\w+) möglich", warning[0].text_content())
    if match:
        data = match.groupdict()
        warnings.append(data)
    elif match_pre:
        data = match_pre.groupdict()
        forewarnings.append(data)
    else:
        continue

if warnings:
    client.set_inverting('side', True)
    client.add_graphics_submessage('side', 'text', text = warnings[0]['what'].upper(), font = "Luminator7_Bold", halign = 'center', top = 1)
    client.add_graphics_submessage('side', 'text', text = warnings[0]['level'].upper(), font = "Luminator5_Bold", halign = 'center', top = 10)
else:
    # Get weather
    owm = pyowm.OWM(API_KEY, language = 'de')
    obs = owm.weather_at_id(2872493)
    w = obs.get_weather()
    icon = 'warning' if forewarnings else w.get_weather_icon_name()
    temp = w.get_temperature('celsius')['temp']
    status = "{what} {level}".format(**forewarnings[0]) if forewarnings else _prepare_status(w.get_detailed_status())
    humidity = w.get_humidity()
    wind = w.get_wind()['speed'] * 3.6 # Speed is in m/sec

    client.set_inverting('side', False)
    client.add_graphics_submessage('side', 'bitmap', image = "bitmaps/weather_icons/{0}.png".format(icon), left = 0, top = 0)
    client.add_graphics_submessage('side', 'text', text = status, font = "Flipdot8_Narrow", left = 18, top = 0)
    client.add_graphics_submessage('side', 'text', text = "{0:.1f}°C {1}% {2:.0f}km/h".format(temp, humidity, wind), font = "Flipdot8_Narrow", left = 18, top = 9)

client.commit()