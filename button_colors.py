"""Button Colour palette for webadmin.

Drop next to routers/ or import from main. GET /button-colors returns this list.
The React Button Colour grid should fetch this instead of a hardcoded swatch array.
Stored value stays hex (e.g. #CD5C5C), matching the current text input.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

BUTTON_COLORS = [
  {
    "id": "color-indian_red",
    "label": "indian red",
    "hex": "#CD5C5C",
    "rgb": [
      205,
      92,
      92
    ]
  },
  {
    "id": "color-light_coral",
    "label": "light coral",
    "hex": "#F08080",
    "rgb": [
      240,
      128,
      128
    ]
  },
  {
    "id": "color-salmon",
    "label": "salmon",
    "hex": "#FA8072",
    "rgb": [
      250,
      128,
      114
    ]
  },
  {
    "id": "color-dark_salmon",
    "label": "dark salmon",
    "hex": "#E9967A",
    "rgb": [
      233,
      150,
      122
    ]
  },
  {
    "id": "color-light_salmon",
    "label": "light salmon",
    "hex": "#FFA07A",
    "rgb": [
      255,
      160,
      122
    ]
  },
  {
    "id": "color-crimson",
    "label": "crimson",
    "hex": "#DC143C",
    "rgb": [
      220,
      20,
      60
    ]
  },
  {
    "id": "color-fire_brick",
    "label": "fire brick",
    "hex": "#B22222",
    "rgb": [
      178,
      34,
      34
    ]
  },
  {
    "id": "color-dark_red",
    "label": "dark red",
    "hex": "#8B0000",
    "rgb": [
      139,
      0,
      0
    ]
  },
  {
    "id": "color-pink",
    "label": "pink",
    "hex": "#FFC0CB",
    "rgb": [
      255,
      192,
      203
    ]
  },
  {
    "id": "color-light_pink",
    "label": "light pink",
    "hex": "#FFB6C1",
    "rgb": [
      255,
      182,
      193
    ]
  },
  {
    "id": "color-hot_pink",
    "label": "hot pink",
    "hex": "#FF69B4",
    "rgb": [
      255,
      105,
      180
    ]
  },
  {
    "id": "color-deep_pink",
    "label": "deep pink",
    "hex": "#FF1493",
    "rgb": [
      255,
      20,
      147
    ]
  },
  {
    "id": "color-medium_violet_red",
    "label": "medium violet red",
    "hex": "#C71585",
    "rgb": [
      199,
      21,
      133
    ]
  },
  {
    "id": "color-pale_violet_red",
    "label": "pale violet red",
    "hex": "#DB7093",
    "rgb": [
      219,
      112,
      147
    ]
  },
  {
    "id": "color-coral",
    "label": "coral",
    "hex": "#FF7F50",
    "rgb": [
      255,
      127,
      80
    ]
  },
  {
    "id": "color-tomato",
    "label": "tomato",
    "hex": "#FF6347",
    "rgb": [
      255,
      99,
      71
    ]
  },
  {
    "id": "color-orange_red",
    "label": "orange red",
    "hex": "#FF4500",
    "rgb": [
      255,
      69,
      0
    ]
  },
  {
    "id": "color-gold",
    "label": "gold",
    "hex": "#FFD700",
    "rgb": [
      255,
      215,
      0
    ]
  },
  {
    "id": "color-light_yellow",
    "label": "light yellow",
    "hex": "#FFFFE0",
    "rgb": [
      255,
      255,
      224
    ]
  },
  {
    "id": "color-lemon_chiffon",
    "label": "lemon chiffon",
    "hex": "#FFFACD",
    "rgb": [
      255,
      250,
      205
    ]
  },
  {
    "id": "color-light_goldenrod_yellow",
    "label": "light goldenrod yellow",
    "hex": "#FAFAD2",
    "rgb": [
      250,
      250,
      210
    ]
  },
  {
    "id": "color-papaya_whip",
    "label": "papaya whip",
    "hex": "#FFEFD5",
    "rgb": [
      255,
      239,
      213
    ]
  },
  {
    "id": "color-moccasin",
    "label": "moccasin",
    "hex": "#FFE4B5",
    "rgb": [
      255,
      228,
      181
    ]
  },
  {
    "id": "color-peach_puff",
    "label": "peach puff",
    "hex": "#FFDAB9",
    "rgb": [
      255,
      218,
      185
    ]
  },
  {
    "id": "color-pale_goldenrod",
    "label": "pale goldenrod",
    "hex": "#EEE8AA",
    "rgb": [
      238,
      232,
      170
    ]
  },
  {
    "id": "color-khaki",
    "label": "khaki",
    "hex": "#F0E68C",
    "rgb": [
      240,
      230,
      140
    ]
  },
  {
    "id": "color-dark_khaki",
    "label": "dark khaki",
    "hex": "#BDB76B",
    "rgb": [
      189,
      183,
      107
    ]
  },
  {
    "id": "color-lavender",
    "label": "lavender",
    "hex": "#E6E6FA",
    "rgb": [
      230,
      230,
      250
    ]
  },
  {
    "id": "color-thistle",
    "label": "thistle",
    "hex": "#D8BFD8",
    "rgb": [
      216,
      191,
      216
    ]
  },
  {
    "id": "color-plum",
    "label": "plum",
    "hex": "#DDA0DD",
    "rgb": [
      221,
      160,
      221
    ]
  },
  {
    "id": "color-violet",
    "label": "violet",
    "hex": "#EE82EE",
    "rgb": [
      238,
      130,
      238
    ]
  },
  {
    "id": "color-orchid",
    "label": "orchid",
    "hex": "#DA70D6",
    "rgb": [
      218,
      112,
      214
    ]
  },
  {
    "id": "color-fuchsia",
    "label": "fuchsia",
    "hex": "#FF00FF",
    "rgb": [
      255,
      0,
      255
    ]
  },
  {
    "id": "color-magenta",
    "label": "magenta",
    "hex": "#FF00FF",
    "rgb": [
      255,
      0,
      255
    ]
  },
  {
    "id": "color-medium_orchid",
    "label": "medium orchid",
    "hex": "#BA55D3",
    "rgb": [
      186,
      85,
      211
    ]
  },
  {
    "id": "color-blue_violet",
    "label": "blue violet",
    "hex": "#8A2BE2",
    "rgb": [
      138,
      43,
      226
    ]
  },
  {
    "id": "color-dark_violet",
    "label": "dark violet",
    "hex": "#9400D3",
    "rgb": [
      148,
      0,
      211
    ]
  },
  {
    "id": "color-dark_orchid",
    "label": "dark orchid",
    "hex": "#9932CC",
    "rgb": [
      153,
      50,
      204
    ]
  },
  {
    "id": "color-dark_magenta",
    "label": "dark magenta",
    "hex": "#8B008B",
    "rgb": [
      139,
      0,
      139
    ]
  },
  {
    "id": "color-purple",
    "label": "purple",
    "hex": "#800080",
    "rgb": [
      128,
      0,
      128
    ]
  },
  {
    "id": "color-rebecca_purple",
    "label": "rebecca purple",
    "hex": "#663399",
    "rgb": [
      102,
      51,
      153
    ]
  },
  {
    "id": "color-medium_slate_blue",
    "label": "medium slate blue",
    "hex": "#7B68EE",
    "rgb": [
      123,
      104,
      238
    ]
  },
  {
    "id": "color-slate_blue",
    "label": "slate blue",
    "hex": "#6A5ACD",
    "rgb": [
      106,
      90,
      205
    ]
  },
  {
    "id": "color-dark_slate_blue",
    "label": "dark slate blue",
    "hex": "#483D8B",
    "rgb": [
      72,
      61,
      139
    ]
  },
  {
    "id": "color-pale_green",
    "label": "pale green",
    "hex": "#98FB98",
    "rgb": [
      152,
      251,
      152
    ]
  },
  {
    "id": "color-light_green",
    "label": "light green",
    "hex": "#90EE90",
    "rgb": [
      144,
      238,
      144
    ]
  },
  {
    "id": "color-medium_spring_green",
    "label": "medium spring green",
    "hex": "#00FA9A",
    "rgb": [
      0,
      250,
      154
    ]
  },
  {
    "id": "color-spring_green",
    "label": "spring green",
    "hex": "#00FF7F",
    "rgb": [
      0,
      255,
      127
    ]
  },
  {
    "id": "color-medium_sea_green",
    "label": "medium sea green",
    "hex": "#3CB371",
    "rgb": [
      60,
      179,
      113
    ]
  },
  {
    "id": "color-sea_green",
    "label": "sea green",
    "hex": "#2E8B57",
    "rgb": [
      46,
      139,
      87
    ]
  },
  {
    "id": "color-forest_green",
    "label": "forest green",
    "hex": "#228B22",
    "rgb": [
      34,
      139,
      34
    ]
  },
  {
    "id": "color-dark_green",
    "label": "dark green",
    "hex": "#006400",
    "rgb": [
      0,
      100,
      0
    ]
  },
  {
    "id": "color-yellow_green",
    "label": "yellow green",
    "hex": "#9ACD32",
    "rgb": [
      154,
      205,
      50
    ]
  },
  {
    "id": "color-olive_drab",
    "label": "olive drab",
    "hex": "#6B8E23",
    "rgb": [
      107,
      142,
      35
    ]
  },
  {
    "id": "color-olive",
    "label": "olive",
    "hex": "#808000",
    "rgb": [
      128,
      128,
      0
    ]
  },
  {
    "id": "color-dark_olive_green",
    "label": "dark olive green",
    "hex": "#556B2F",
    "rgb": [
      85,
      107,
      47
    ]
  },
  {
    "id": "color-medium_aquamarine",
    "label": "medium aquamarine",
    "hex": "#66CDAA",
    "rgb": [
      102,
      205,
      170
    ]
  },
  {
    "id": "color-dark_sea_green",
    "label": "dark sea green",
    "hex": "#8FBC8F",
    "rgb": [
      143,
      188,
      143
    ]
  },
  {
    "id": "color-light_sea_green",
    "label": "light sea green",
    "hex": "#20B2AA",
    "rgb": [
      32,
      178,
      170
    ]
  },
  {
    "id": "color-dark_cyan",
    "label": "dark cyan",
    "hex": "#008B8B",
    "rgb": [
      0,
      139,
      139
    ]
  },
  {
    "id": "color-teal",
    "label": "teal",
    "hex": "#008080",
    "rgb": [
      0,
      128,
      128
    ]
  },
  {
    "id": "color-aqua",
    "label": "aqua",
    "hex": "#00FFFF",
    "rgb": [
      0,
      255,
      255
    ]
  },
  {
    "id": "color-cyan",
    "label": "cyan",
    "hex": "#00FFFF",
    "rgb": [
      0,
      255,
      255
    ]
  },
  {
    "id": "color-light_cyan",
    "label": "light cyan",
    "hex": "#E0FFFF",
    "rgb": [
      224,
      255,
      255
    ]
  },
  {
    "id": "color-pale_turquoise",
    "label": "pale turquoise",
    "hex": "#AFEEEE",
    "rgb": [
      175,
      238,
      238
    ]
  },
  {
    "id": "color-aquamarine",
    "label": "aquamarine",
    "hex": "#7FFFD4",
    "rgb": [
      127,
      255,
      212
    ]
  },
  {
    "id": "color-turquoise",
    "label": "turquoise",
    "hex": "#40E0D0",
    "rgb": [
      64,
      224,
      208
    ]
  },
  {
    "id": "color-medium_turquoise",
    "label": "medium turquoise",
    "hex": "#48D1CC",
    "rgb": [
      72,
      209,
      204
    ]
  },
  {
    "id": "color-dark_turquoise",
    "label": "dark turquoise",
    "hex": "#00CED1",
    "rgb": [
      0,
      206,
      209
    ]
  },
  {
    "id": "color-cadet_blue",
    "label": "cadet blue",
    "hex": "#5F9EA0",
    "rgb": [
      95,
      158,
      160
    ]
  },
  {
    "id": "color-steel_blue",
    "label": "steel blue",
    "hex": "#4682B4",
    "rgb": [
      70,
      130,
      180
    ]
  },
  {
    "id": "color-light_steel_blue",
    "label": "light steel blue",
    "hex": "#B0C4DE",
    "rgb": [
      176,
      196,
      222
    ]
  },
  {
    "id": "color-powder_blue",
    "label": "powder blue",
    "hex": "#B0E0E6",
    "rgb": [
      176,
      224,
      230
    ]
  },
  {
    "id": "color-light_blue",
    "label": "light blue",
    "hex": "#ADD8E6",
    "rgb": [
      173,
      216,
      230
    ]
  },
  {
    "id": "color-sky_blue",
    "label": "sky blue",
    "hex": "#87CEEB",
    "rgb": [
      135,
      206,
      235
    ]
  },
  {
    "id": "color-light_sky_blue",
    "label": "light sky blue",
    "hex": "#87CEFA",
    "rgb": [
      135,
      206,
      250
    ]
  },
  {
    "id": "color-deep_sky_blue",
    "label": "deep sky blue",
    "hex": "#00BFFF",
    "rgb": [
      0,
      191,
      255
    ]
  },
  {
    "id": "color-blue",
    "label": "blue",
    "hex": "#0000FF",
    "rgb": [
      0,
      0,
      255
    ]
  },
  {
    "id": "color-medium_blue",
    "label": "medium blue",
    "hex": "#0000CD",
    "rgb": [
      0,
      0,
      205
    ]
  },
  {
    "id": "color-dark_blue",
    "label": "dark blue",
    "hex": "#00008B",
    "rgb": [
      0,
      0,
      139
    ]
  },
  {
    "id": "color-navy",
    "label": "navy",
    "hex": "#000080",
    "rgb": [
      0,
      0,
      128
    ]
  },
  {
    "id": "color-midnight_blue",
    "label": "midnight blue",
    "hex": "#191970",
    "rgb": [
      25,
      25,
      112
    ]
  },
  {
    "id": "color-cornsilk",
    "label": "cornsilk",
    "hex": "#FFF8DC",
    "rgb": [
      255,
      248,
      220
    ]
  },
  {
    "id": "color-blanched_almond",
    "label": "blanched almond",
    "hex": "#FFEBCD",
    "rgb": [
      255,
      235,
      205
    ]
  },
  {
    "id": "color-bisque",
    "label": "bisque",
    "hex": "#FFE4C4",
    "rgb": [
      255,
      228,
      196
    ]
  },
  {
    "id": "color-navajo_white",
    "label": "navajo white",
    "hex": "#FFDEAD",
    "rgb": [
      255,
      222,
      173
    ]
  },
  {
    "id": "color-wheat",
    "label": "wheat",
    "hex": "#F5DEB3",
    "rgb": [
      245,
      222,
      179
    ]
  },
  {
    "id": "color-burly_wood",
    "label": "burly wood",
    "hex": "#DEB887",
    "rgb": [
      222,
      184,
      135
    ]
  },
  {
    "id": "color-tan",
    "label": "tan",
    "hex": "#D2B48C",
    "rgb": [
      210,
      180,
      140
    ]
  },
  {
    "id": "color-rosy_brown",
    "label": "rosy brown",
    "hex": "#BC8F8F",
    "rgb": [
      188,
      143,
      143
    ]
  },
  {
    "id": "color-sandy_brown",
    "label": "sandy brown",
    "hex": "#F4A460",
    "rgb": [
      244,
      164,
      96
    ]
  },
  {
    "id": "color-goldenrod",
    "label": "goldenrod",
    "hex": "#DAA520",
    "rgb": [
      218,
      165,
      32
    ]
  },
  {
    "id": "color-dark_goldenrod",
    "label": "dark goldenrod",
    "hex": "#B8860B",
    "rgb": [
      184,
      134,
      11
    ]
  },
  {
    "id": "color-peru",
    "label": "peru",
    "hex": "#CD853F",
    "rgb": [
      205,
      133,
      63
    ]
  },
  {
    "id": "color-chocolate",
    "label": "chocolate",
    "hex": "#D2691E",
    "rgb": [
      210,
      105,
      30
    ]
  },
  {
    "id": "color-saddle_brown",
    "label": "saddle brown",
    "hex": "#8B4513",
    "rgb": [
      139,
      69,
      19
    ]
  },
  {
    "id": "color-sienna",
    "label": "sienna",
    "hex": "#A0522D",
    "rgb": [
      160,
      82,
      45
    ]
  },
  {
    "id": "color-brown",
    "label": "brown",
    "hex": "#A52A2A",
    "rgb": [
      165,
      42,
      42
    ]
  },
  {
    "id": "color-maroon",
    "label": "maroon",
    "hex": "#800000",
    "rgb": [
      128,
      0,
      0
    ]
  },
  {
    "id": "color-black",
    "label": "black",
    "hex": "#000000",
    "rgb": [
      0,
      0,
      0
    ]
  }
]


@router.get("/button-colors", response_class=JSONResponse)
async def button_colors():
    return BUTTON_COLORS
