import re
import os
from sys import argv
from statistics import mean, median
from functools import cmp_to_key
from requests_futures.sessions import FuturesSession

# path to folder containing decks
DECK_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "decks")

# all kinds of elements of a cards
ELEMENTS = [
    "amount",
    "name",
    "set",
    "number",
    "foil",
    "language",
    "cost",
    "cmc",
    "color",
    "type",
    "subtype",
    "rarity",
    "price",
    "tcg_price",
    "modern",
    "commander",
]
# all kinds of elements stored in file
RAW_ELEMENTS = [
    "amount",
    "set",
    "number",
    "foil",
    "language",
]
# all kinds of numeric elements
COUNTABLE_ELEMENTS = [
    "amount",
    "cmc",
    "price",
    "tcg_price",
]
# global stats applicable to countable elements
ELEMENT_MODIFIERS = [
    "-total-",
    "-max-",
    "-min-",
    "-avg-",
    "-median-",
]
# filters applicable to uncountable elements
ELEMENT_FILTERS = [
    "=",
    "!=",
    ":",
    "!:",
]
# filters applicable to countable elements
COUNTABLE_FILTERS = [
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
]

decks = []  # all decks to search
elements = []  # all elements to print
modified = []  # all global stats to print
filters = []  # all filters applied to cards
list_decks = False  # wether to list available decks
unique = False  # wether to print number of unique matches

# parse arguments
for arg in argv[1:]:
    # 0: everything before and including last '-'
    # 1: everything before next '?' or filter operator
    # 2: optional '?' character
    # 3: filter operator and filter value repeated any number of times if separated by '/'
    a = re.match("^(.*-)?([^?=!:<>]+)?(\?)?((?:[=!:<>]+[^=!:<>/]+/?)+)?(?<!/)$", arg)

    if a is not None:
        m, e, q, f = a.groups()
        # split into tuples of filter operators and filter values
        f = (
            None
            if f is None
            else [re.match("^([=!:<>]+)([^=!:<>]+)$", x).groups() for x in f.split("/")]
        )
        a = m, e, q, f

    match a:
        # modified element
        case (m, e, None, None) if m in ELEMENT_MODIFIERS and e in COUNTABLE_ELEMENTS:
            modified.append((e, m))
        # filtered countable element
        case ("-", e, q, l) if e in COUNTABLE_ELEMENTS and l is not None and all(
            f in COUNTABLE_FILTERS and re.match("\\d*\\.?\\d*", v) for f, v in l
        ):
            filters.append((e, [(f, float(v)) for f, v in l]))
            if q is None:
                elements.append(e)
        # filtered uncountable element
        case (
            "-",
            e,
            q,
            l,
        ) if e in ELEMENTS and l is not None and e not in COUNTABLE_ELEMENTS and all(
            f in ELEMENT_FILTERS for f, _ in l
        ):
            filters.append((e, [(f, v.replace("_", " ")) for f, v in l]))
            if q is None:
                elements.append(e)
        # unfiltered element
        case ("-", e, None, None) if e in ELEMENTS:
            elements.append(e)
        # decks flag
        case ("-", "decks", None, None):
            list_decks = True
        # unique modifier flag
        case ("-", "unique", None, None):
            unique = True
        # asterisk for all cards
        case (None, "*", None, None):
            decks.append("collection")
        # deck
        case (None, e, None, None):
            decks.append(e)
        # unable to match
        case _:
            print(f"ERROR: '{arg}' is not a recognized command")
            exit(1)

# read cards from files
cards = []
for d in decks:
    file = os.path.join(DECK_DIR, d + ".txt")
    # make sure file exists
    if not os.path.isfile(file):
        print(f"ERROR: File '{file}' not found")
        exit(1)

    with open(file, "r", encoding="utf-8") as f:
        for line in f:
            # columns are tab separated as per excel syntax
            amount, card_set, number, foil, language = line.rstrip("\n").split("\t")
            # add card from line
            cards.append(
                {
                    "amount": int(amount),
                    "set": card_set,
                    "number": number,
                    "foil": foil == "TRUE",
                    "language": language,
                }
            )

# read cards from web only if necessary
if any(e not in RAW_ELEMENTS for e in elements + [e for e, _ in modified + filters]):
    session = FuturesSession()
    links = [  # grab information from scryfall.com
        "https://scryfall.com/card/{set}/{number}/{language}".format(**c) for c in cards
    ]
    # load all links at once
    futures = [session.get(link) for link in links]
    responses = [future.result() for future in futures]
    status_codes = [r.status_code for r in responses]
    # make sure all responses are good
    if any(s != 200 for s in status_codes):
        print(
            "ERROR: Response status code:",
            ", ".join(f"{s} from {l}" for l, s in zip(links, status_codes) if s != 200),
        )
        exit(1)
    # get text from the sites
    sites = [r.text for r in responses]

    # the cards the missing elements
    for c, html in zip(cards, sites):
        c["name"] = re.sub(
            "&#39;",
            "'",
            re.search(
                'class="card-text-card-name".*?>(.*?)</span>',
                html,
                re.S,
            ).group(1),
        ).strip()

        cost = re.search('class="card-text-mana-cost".*?>(.*?)</span>', html, re.S)
        c["cost"] = (
            "0" if cost is None else re.sub("(^|}).*?({|$)", "", cost.group(1)).strip()
        )

        c["cmc"] = int("0" + re.sub("[^0-9]", "", c["cost"])) + len(
            re.sub("[^WUBRG]", "", c["cost"])
        )
        c["color"] = " ".join(
            v
            for k, v in zip("WUBRG", ["White", "Blue", "Black", "Red", "Green"])
            if k in c["cost"]
        )

        types = (
            re.search('class="card-text-type-line".*?>(.*?)<', html, re.S)
            .group(1)
            .split("—")
        )
        c["type"] = types[0].strip()
        c["subtype"] = "-" if len(types) < 2 else types[1].strip()

        c["rarity"] = (
            re.search('class="prints-current-set-details".*?·(.*?)·', html, re.S)
            .group(1)
            .strip()
        )

        price = re.search(
            "Buy" + (" foil " if c["foil"] else " ") + "on Cardmarket.*?€(.*?)<",
            html,
            re.S,
        )
        c["price"] = 0 if price is None else float(price.group(1).strip())

        tcg_price = re.search(
            "Buy" + (" foil " if c["foil"] else " ") + "on TCGplayer.*?\\$(.*?)<",
            html,
            re.S,
        )
        c["tcg_price"] = 0 if tcg_price is None else float(tcg_price.group(1).strip())

        modern = re.search(
            'class="card-legality-item".*?>.*?<dt>Modern</dt>.*?>(.*?)<', html, re.S
        )
        c["modern"] = False if modern is None else modern.group(1).strip() == "Legal"

        commander = re.search(
            'class="card-legality-item".*?>.*?<dt>Commander</dt>.*?>(.*?)<', html, re.S
        )
        c["commander"] = (
            False if commander is None else commander.group(1).strip() == "Legal"
        )

# apply card filters
func_map = {  # filter function from filter operator
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ":": lambda a, b: b in a,
    "!:": lambda a, b: b not in a,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


def apply_filter(amount):
    global filters, cards

    for e, f in filters:
        # apply most filters before collapsing but amount only after
        if (e == "amount") == amount:
            # only keep the cards passing an option from all filters
            cards = [
                c
                for c in cards
                if any(
                    func_map[o](
                        float(c[e]) if e in COUNTABLE_ELEMENTS else str(c[e]), v
                    )
                    for o, v in f
                )
            ]


apply_filter(False)

# collapse duplicate cards
collapsed = []
for c in cards:
    for o in collapsed:
        # if all relevant elements are equal they can be collapsed into one
        if all(
            c[e] == o[e] for e in elements + [e[0] for e in modified] if e != "amount"
        ):
            o["amount"] += c["amount"]
            break
    else:
        # if they aren't, it is a separate card
        collapsed.append(c)

cards = collapsed
apply_filter(True)


# sort cards
def cmp(a, b):
    # sort by elements in the order they were specified
    for e in elements:
        if a[e] < b[e]:
            return -1
        elif b[e] < a[e]:
            return 1
    return 0


cards.sort(key=cmp_to_key(cmp))

# print cards
if cards:
    # print prices differently
    styled = [
        {
            **c,
            "price": "-" if "price" not in c or c["price"] == 0 else f"€{c['price']}",
            "tcg_price": "-"
            if "tcg_price" not in c or c["tcg_price"] == 0
            else f"${c['tcg_price']}",
        }
        for c in cards
    ]
    # get least width that will fit all elements in column
    widths = [max(map(lambda c: len(str(c[e])), styled)) for e in elements]
    # create the template to format cards
    template = (" " * 4).join(f"{{{e}:<{w}}}" for e, w in zip(elements, widths))
    # print all cards
    for c in styled:
        print(template.format(**{k: str(v) for k, v in c.items()}))
    # follow by newline
    print()

    def data(e):
        # remove the amount statistic to more easily apply the function
        if e == "amount":
            for c in cards:
                yield c[e]
        else:
            for c in cards:
                for _ in range(c["amount"]):
                    yield c[e]

    # print global statistics
    for e, m in modified:
        # function corresponding to modification
        func = lambda _: []
        match m:
            case "-total-":
                func = sum
            case "-max-":
                func = max
            case "-min-":
                func = min
            case "-avg-":
                func = mean
            case "-median-":
                func = median

        # add unit
        unit = ""
        if e == "price":
            unit = "€"
        elif e == "tcg_price":
            unit = "$"

        # print statistic
        print(f"{m.strip('-').capitalize()} {e}: {unit}{round(func(data(e)),2)}")

    # print amount of unique matches
    if unique:
        print(f"Unique: {len(cards)}")
    # follow by newline
    if unique or modified:
        print()

elif decks:
    print("No matches found\n")


# print all available decks
if list_decks:
    print("Saved decks:")
    # print cards in decks list
    for f in os.listdir(DECK_DIR):
        if os.path.isfile(os.path.join(DECK_DIR, f)):
            print(" " * 4 + os.path.splitext(os.path.basename(f))[0])
    print()

exit(0)
