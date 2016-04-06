#!/usr/bin/env python3

import flipdot

client = flipdot.FlipdotClient("localhost")

#client.add_graphics_submessage('panel', 'text', text = "%d%m%y", timestring = True, font = "Itty", size = 4, y = 1, refresh_interval = 'minute')
#client.add_graphics_submessage('panel', 'binary_clock', block_spacing_x = 2, y = 7, refresh_interval = 'minute')
client.add_graphics_submessage('panel', 'text', text = "%H%M", timestring = True, font = "FIS_20", x = 1, refresh_interval = 'minute')

client.add_graphics_submessage('side', 'text', text = "%d.%m.%y", timestring = True, font = "Itty", size = 4, x = 0, y = 0, refresh_interval = 'minute')
client.add_graphics_submessage('side', 'binary_clock', block_width = 4, block_height = 4, x = 0, y = 7, refresh_interval = 'minute')
client.add_graphics_submessage('side', 'text', text = "%H:%M", timestring = True, size = 22, x = 30, refresh_interval = 'minute')

client.add_graphics_submessage('front', 'text', text = "%H:%M", timestring = True, font = "Arial Black", size = 22, halign = 'right', refresh_interval = 'minute')

client.set_backlight('panel', False)
client.set_backlight('side', False)
client.set_backlight('front', True)

client.commit()