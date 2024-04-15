import os
import re
import json
import asyncio
import aiohttp
import aiofiles
from sys import argv
from statistics import median, mean
from functools import cmp_to_key
from reportlab.pdfgen import canvas

# directory containing decks
DECK_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "decks")
# directory containing images
IMAGE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "images")
# path to generated pdf
PDF_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "mtg.pdf")
# path to generated html
HTML_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "mtg.html")
# size of cards in pdf measured in points
CARD_SIZE = (180, 252)
# column order in raw data
RAW_COLUMNS = ("amount", "set", "number", "lang", "foil")
# format-able link to scryfall api
API_LINK = "https://api.scryfall.com/cards/{set}/{number}/{lang}"
# minimum time in seconds between api request
RATE_LIMIT = 0.1

decks = set()  # all decks to search
outputs = set()  # all decks to output to
elements = []  # all elements to print
stats = []  # all global stats to print
filters = []  # all filters applied to cards
flags = set()  # all flags set
cards = []  # all cards found

# card attribute name: card attribute is numeric
ELEMENTS = {
    "amount": True,
    "foil": False,
    "name": False,
    "lang": False,
    "cost": False,
    "cmc": True,
    "type": False,
    "subtype": False,
    "color": False,
    "identity": False,
    "text": False,
    "modern": False,
    "commander": False,
    "set": False,
    "number": False,
    "rarity": False,
    "fullart": False,
    "usd": True,
    "eur": True,
}

# global numeric attribute: (non-numeric compatibility, numeric compatibility)
PREFIXES = {
    "-total-": (False, True),  # sum of all values
    "-max-": (False, True),  # highest value
    "-min-": (False, True),  # lowest value
    "-avg-": (False, True),  # arithmetic mean
    "-median-": (False, True),  # median value
    "-unique-": (True, True),  # number of unique values
}

# attribute filters: (non-numeric compatibility, numeric compatibility)
FILTERS = {
    "=": (True, True),  # exact match
    "!=": (True, True),  # anything but exact match
    "?": (True, False),  # contains
    "!?": (True, False),  # does not contain
    "<": (False, True),  # strictly less than
    "<=": (False, True),  # less than or equal to
    ">": (False, True),  # strictly greater than
    ">=": (False, True),  # greater than or equal to
}

# standalone flags
FLAGS = {
    "decks",  # print all available decks
    "get",  # get new card information from API_LINK
    "get_img",  # get new card images from scryfall
    "pdf",  # wether to generate pdf with matching cards
    "html",  # wether to generate html with matching cards
    "o",  # use next deck as output instead of input
}

# parse arguments
for arg in argv[1:]:
    prefix, element, show, search = re.match(
        "^(.*-)?([^#=?!<>]+)?(#)?(.+)?$", arg
    ).groups()
    if search is not None:
        search = [re.match("^([=?!<>]+)?(.*)$", x).groups() for x in search.split("/")]

    match (prefix, element, show, search):
        # stat element
        case (p, e, None, None) if p in PREFIXES and e in ELEMENTS and PREFIXES[p][
            ELEMENTS[e]
        ]:
            stats.append((e, p))
        # filtered countable element
        case ("-", e, q, l) if e in ELEMENTS and ELEMENTS[e] and l and all(
            f in FILTERS and FILTERS[f][1] for f, _ in l
        ):
            for _, v in l:
                if not re.match("^[0-9]+(.[0-9]+)?$", v):
                    print(f"ERROR: '{v}' is not a number")
                    exit(1)

            filters.append((e, [(f, float(v)) for f, v in l]))
            if q is None:
                elements.append(e)
        # filtered uncountable element
        case ("-", e, q, l) if e in ELEMENTS and not ELEMENTS[e] and l and all(
            f in FILTERS and FILTERS[f][0] for f, _ in l
        ):
            filters.append((e, l))
            if q is None:
                elements.append(e)
        # unfiltered element
        case ("-", e, None, None) if e in ELEMENTS:
            elements.append(e)
        # flags
        case ("-", f, None, None) if f in FLAGS:
            flags.add(f)
        # deck
        case (None, e, None, None):
            if "o" in flags:
                outputs.add(e)
                flags.remove("o")  # don't output all following
            else:
                decks.add(e)
        # unable to match
        case _:
            print(f"ERROR: '{arg}' is not a recognized command")
            exit(1)


# get raw card from API_LINK using session
async def get_card(card, session):
    url = API_LINK.format(**card)
    async with session.get(url) as response:
        res = await response.json()
        if response.ok:
            return res
        else:
            print(f"ERROR: '{url}' returned http code {response.status}")
            return None


# get raw cards in parallel
async def get_cards(cards):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for card in cards:
            task = asyncio.create_task(get_card(card, session))
            tasks.append(task)
            # be nice to their server (and don't get a rate limit)
            await asyncio.sleep(RATE_LIMIT)
        return await asyncio.gather(*tasks)


# save images
async def save_image(card, session):
    url = card["img"]
    img_path = os.path.join(IMAGE_DIR, card["id"] + ".jpg")
    async with session.get(url) as response:
        res = await response.read()
        if response.ok:
            async with aiofiles.open(img_path, "wb") as f:
                await f.write(res)
            return True
        else:
            print(f"ERROR: '{url}' returned http code {response.status}")
            return False


# save images in parallel
async def save_images(cards):
    async with aiohttp.ClientSession() as session:
        tasks = [save_image(card, session) for card in cards]
        return await asyncio.gather(*tasks)


# get card data
for deck in decks:
    if not os.path.isdir(os.path.join(DECK_DIR, deck)):
        print(f"ERROR: Could not find deck '{deck}'")
        exit(1)

    # path to raw card data
    raw_path = os.path.join(DECK_DIR, deck, "raw.txt")
    # path to json with api data
    data_path = os.path.join(DECK_DIR, deck, "data.json")

    if "get" not in flags:  # load from file
        if not os.path.isfile(data_path):  # file not found
            print(f"ERROR: Could not find '{data_path}'. Use '-get' to generate it")
            exit(1)

        with open(data_path, "r", encoding="utf-8") as data_file:
            try:  # add new cards as dictionaries
                cards.extend(json.loads(data_file.read()))
            except ValueError as e:  # failed to load json
                print(
                    f"ERROR: '{data_path}' contains invalid json. Use '-get' to generate it"
                )
                exit(1)

    else:  # get cards from api
        if not os.path.isfile(raw_path):  # file not found
            print(f"ERROR: Could not find '{raw_path}'")
            exit(1)

        with (
            open(raw_path, "r", encoding="utf-8") as raw_file,
            open(data_path, "w", encoding="utf-8") as data_file,
        ):
            raw = [  # split columns into dictionary
                dict(zip(RAW_COLUMNS, line.rstrip("\n").split("\t")))
                for line in raw_file
            ]

            # check if any columns missing
            if any(len(line) < len(RAW_COLUMNS) for line in raw):
                print(f"ERROR: too few columns in '{raw_path}'")
                exit(1)

            for line in raw:  # check that all amounts are valid
                if not line["amount"].isdigit():
                    print(f"ERROR: '{line['amount']}' is not a valid amount")
                    exit(1)

            # combine duplicates
            collapsed_raw = []
            for r in raw:
                r["amount"] = int(r["amount"])
                r["foil"] = r["foil"].lower()
                for c in collapsed_raw:
                    # amount does not need to match
                    if all(r[e] == c[e] for e in RAW_COLUMNS if e != "amount"):
                        c["amount"] += r["amount"]
                        break
                else:
                    # if no match found, it's a separate card
                    collapsed_raw.append(r)
            raw = collapsed_raw

            # get multiple cards at once
            scry = asyncio.run(get_cards(raw))
            if None in scry:  # bad response
                exit(1)

            data = []  # all cards from deck
            for r, s in zip(raw, scry):
                if "card_faces" in s:  # default to front face on double sided cards
                    s = s["card_faces"][0] | s

                foil = r["foil"] == "true"  # wether card is foil or not
                types = s["type_line"].split(" \u2014 ")  # (type, subtype)
                usd = s["prices"]["usd_foil" if foil else "usd"]
                eur = s["prices"]["eur_foil" if foil else "eur"]
                data.append(  # process information about card
                    {
                        "amount": int(r["amount"]),
                        "foil": foil,
                        "name": s["name"],
                        "lang": s["lang"],
                        "cost": re.sub("[^WUBRG0-9]", "", s["mana_cost"]),
                        "cmc": int(s["cmc"]),
                        "type": types[0],
                        "subtype": types[1] if len(types) > 1 else None,
                        "color": "".join(s["colors"]),
                        "identity": "".join(s["color_identity"]),
                        "text": s["oracle_text"],
                        "modern": s["legalities"]["modern"] == "legal",
                        "commander": s["legalities"]["commander"] == "legal",
                        "set": s["set_name"],
                        "number": s["collector_number"],
                        "rarity": s["rarity"],
                        "fullart": s["full_art"],
                        "usd": None if usd is None else float(usd),
                        "eur": None if eur is None else float(eur),
                        "img": s["image_uris"]["normal"],
                        "id": s["id"],
                        "link": s["scryfall_uri"],
                        "raw": "\t".join(str(r[e]) for e in RAW_COLUMNS),
                    }
                )

            # save data to not have to -get next time
            data_file.write(json.dumps(data))
            cards.extend(data)

# download all images at once
if "get_img" in flags:
    # create directory if necessary
    if not os.path.isdir(IMAGE_DIR):
        os.makedirs(IMAGE_DIR)

    status = asyncio.run(save_images(cards))
    if not all(status):  # bad response
        exit(1)

# apply card filters
filter_map = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "?": lambda a, b: b in a,
    "!?": lambda a, b: b not in a,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}

for e, f in filters:
    # collapse before amount filter
    if e == "amount":
        continue
    # only keep the cards passing an option from all filters
    cards = [
        c
        for c in cards
        if c[e] is not None and any(filter_map[o](c[e], v) for o, v in f)
    ]

# collapse duplicate cards
collapsed = []
for i, c in enumerate(cards):
    for o in collapsed:
        # if all relevant elements are equal they can be collapsed into one
        if all(c[e] == o[e] for e in elements if e != "amount"):
            o["amount"] += c["amount"]
            o["_ids"].append(i)
            break
    else:
        # if they aren't, it is a separate card
        collapsed.append(c | {"_ids": [i]})

# filter amount after collapse
for e, f in filters:
    if e != "amount":
        continue
    collapsed = [c for c in collapsed if any(filter_map[o](c[e], v) for o, v in f)]
    cards = [c for i, c in enumerate(cards) if any(i in d["_ids"] for d in collapsed)]


# sort cards
def cmp(a, b):
    # sort by elements in the order they were specified
    for e in elements:
        if a[e] is b[e] is None:
            continue
        elif a[e] is None:
            return -1
        elif b[e] is None:
            return 1
        elif a[e] < b[e]:
            return -1
        elif b[e] < a[e]:
            return 1
    return 0


cards.sort(key=cmp_to_key(cmp))
collapsed.sort(key=cmp_to_key(cmp))


if cards:
    # print prices differently
    if elements:
        styled = [
            {
                k: str(v)
                for k, v in {
                    **c,
                    "usd": "-" if c["usd"] is None else f"${c['usd']}",
                    "eur": "-" if c["eur"] is None else f"€{c['eur']}",
                    "subtype": c["subtype"] or "-",
                    "color": c["color"] or "-",
                    "identity": c["identity"] or "-",
                    "cost": c["cost"] or "0",
                }.items()
            }
            for c in collapsed
        ]
        # get least width that will fit all elements in column
        widths = [max(map(lambda c: len(c[e]), styled)) for e in elements]
        # create the template to format cards
        template = "    ".join(f"{{{e}:<{w}}}" for e, w in zip(elements, widths))
        # print all cards
        for c in styled:
            print(template.format(**c))
        # follow by newline
        print()

    def expand(e):
        # remove the amount statistic to more easily apply the function
        if e == "amount":
            for c in cards:
                yield c[e]
        else:
            for c in cards:
                if c[e] is not None:
                    for _ in range(c["amount"]):
                        yield c[e]

    # print global statistics
    for e, m in stats:
        # function corresponding to modification
        stat_map = {
            "-total-": sum,
            "-max-": max,
            "-min-": min,
            "-avg-": mean,
            "-median-": median,
            "-unique-": lambda l: len(set(l)),
        }[m]

        # add unit
        unit = ""
        if e == "usd":
            unit = "$"
        elif e == "eur":
            unit = "€"

        # print statistic
        print(f"{m.strip('-').capitalize()} {e}: {unit}{round(stat_map(expand(e)),2)}")

    # follow by newline
    if stats:
        print()

elif decks:
    print("No matches found\n")

# generate pdf with matching cards
if "pdf" in flags:
    pdf = canvas.Canvas(PDF_PATH)
    pdf.setTitle("Magic: The Gathering")

    # check so all images are downloaded
    images = [os.path.join(IMAGE_DIR, card["id"] + ".jpg") for card in cards]
    if not all(os.path.isfile(i) for i in images):
        print("ERROR: Could not find images. Use '-get_img' to generate it")
        exit(1)

    x, y = 0, 0
    for image in images:
        # add image
        pdf.drawInlineImage(
            image,
            x,
            pdf._pagesize[1] - y - CARD_SIZE[1],
            CARD_SIZE[0],
            CARD_SIZE[1],
        )
        # new column
        x += CARD_SIZE[0]
        if x + CARD_SIZE[0] > pdf._pagesize[0]:
            # new line
            x = 0
            y += CARD_SIZE[1]
            if y + CARD_SIZE[1] > pdf._pagesize[1]:
                # new page
                y = 0
                pdf.showPage()
    if y != 0:
        pdf.showPage()
    pdf.save()

    print(f"PDF saved at '{PDF_PATH}'")

# generate html page with matching cards
if "html" in flags:
    with open(HTML_PATH, mode="w", encoding="utf-8") as f:
        f.writelines(
            (  # basic document structure
                "<!DOCTYPE html>\n",
                '<html lang="en">\n',
                "<head>\n",
                '    <meta charset="UTF-8">\n',
                "    <title>Magic: The Gathering</title>\n",
                "</head>\n",
                "<body>\n",
                *(
                    # images with links
                    '    <a href="{href}"><img src="file:///{src}" alt="{alt}"></a>\n'.format(
                        src=os.path.join(IMAGE_DIR, card["id"] + ".jpg"),
                        alt=card["name"],
                        href=card["link"],
                    )
                    for card in cards
                ),
                "</body>\n",
                "</html>\n",
            )
        )

    print(f"HTML document saved at '{HTML_PATH}'")

# add newline
if "pdf" in flags or "html" in flags:
    print()


# recursively print decks in directory
def print_decks(dir, depth=1):
    for f in os.listdir(dir):
        full_path = os.path.join(dir, f)
        if os.path.isdir(full_path):
            print("    " * depth + f)
            print_decks(full_path, depth + 1)


# print all available decks
if "decks" in flags:
    print("Saved decks:")
    print_decks(DECK_DIR)
    print()

# print to console if no file set
if "o" in flags:
    print("\n".join(c["raw"] for c in cards), end="\n\n")

# output cards to decks
for deck in outputs:
    dir_path = os.path.join(DECK_DIR, deck)
    file_path = os.path.join(DECK_DIR, deck, "raw.txt")

    # create dir if not present
    if not os.path.isdir(dir_path):
        os.makedirs(dir_path)

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines((c["raw"] + "\n") for c in cards)

exit(0)
