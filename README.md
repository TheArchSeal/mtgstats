# Scrython

***CLI MTG deck analysis tool using Scryfall api***

## Decks

To specify which decks to use, pass the name of the deck as a command-line argument. To do this simply add `<deck>` when running the script.

## Card Attributes

Attributes can be passed as command-line arguments to select which will be printed. They must be prefixed with '-' to make the full argument `-<attrubte>`. By default no attributes are selected. Additionally, the output will be sorted by the selected attributes in ascending order, sorting by earlier selected attributes first.

### Numeric

* **amount** - number of cards where all other printed attributes are equal
* **cmc** - the cards converted mana cost
* **eur** - price in euros on Cardmarket
* **usd** - price in US dollars on TCGplayer

### Non-Numeric

* **foil** - wether the card is foil
* **name** - the english name of the card
* **lang** - the language the card is printed in
* **cost** - the mana cost of the card
* **type** - all the cards types separated by space
* **subtype** - all the cards subtypes separated by space
* **color** - the color of card
* **identity** - the color identity of the card
* **text** - the card text
* **modern** - wether it is modern legal
* **commander** - wether it is commander legal
* **set** - name of the set teh card was printed in
* **number** - the collector number of the card
* **rarity** - the rarity the card was printed in
* **fullart** - wether the card is fullart

## Attribute Filters

To filter cards based on their attribute, append the filter and value to the attribute flag like `-<attribute><filter><value>`. To filter by an attribute without printing it, add '#' between the attribute and filter making the argument `-<attribute>#<filter><value>`. Multiple filters can be separated by '/' to filter for cards matching at least one of them like `-<attribute><filter1><value1>/<filter2><value2>/...`. Multiple filters can be specified for different attributes or different flags of the same argument to filter for cards matching all of them.

### Numeric

* **=** - equality
* **!=** - inequality
* **<** - strictly less than
* **<=** - less than or equal to
* **>** - strictly greater than
* **>=** - greater than or equal to

### Non-Numeric

* **=** - exact match
* **!=** - exclude exact match
* **?** - contains
* **!?** - does not contain

## Global Attributes

A non-filtered attribute can be prefixed by a global attribute to print statistics on all matching cards with the syntax `-<prefix>-<attribute>`.

### Numeric

* **total** - sum of attribute values
* **max** - maximum value
* **min** - minimum value
* **avg** - arithmetic mean of values
* **median** - median value
* **unique** - number of unique values

### Non-Numeric

* **unique** - number of unique values

## Independent Flags

These flags are added as command-line arguments without relating to attributes though their syntax is the same `-<flag>`.

* **decks** - print all available decks
* **get** -  download new card information from scryfall
* **get_img** - download new card images from scryfall
* **pdf** - create pdf with matching cards
* **html** - create html document with matching cards
* **o** - the next deck will instead be written over with the raw card data

## File Structure

All decks are stored under the 'decks' folder in the same directory as the script. They are themselves folders that can store nested decks. A decks name, nested or not, is its path relative to the 'decks' folder so the name of a top-level deck is simply `<deck>` and a nested deck is `<deck>/.../<sub-deck>`.

```
.
│   mtg.py
│   mtg.pdf
│   mtg.html
│
├── images
│   │   <id>.jpg
│   ...
│
└── decks
    ├── <deck>
    │   │   raw.txt
    │   │   data.json
    │   │
    │   ├── <sub-deck>
    ... │   │   raw.txt
        ... │   data.json
            ...
```

### mtg.py

The program itself, written in Python 3.12 but might work for earlier versions. It requires the following libraries:
* aiofiles
* aiohttp
* reportlab

### raw.txt

Each deck must include a file named 'raw.txt'. This file contains a line for each card in the deck with five tab-separated columns that specify:

1. Number of copies of the card
2. Three character abbreviation of the set it was printed in
3. Collector number of the card from its set
4. Two character abbreviation of the language it was printed in
5. Whether the card is foil or not

### data.json

Unlike 'raw.txt', 'data.json' does not need to be manually created. It is generated after using the `-get` flag and stores all necessary information retrieved from scryfall. So long as the file exists the program can use card data without downloading it, though card prices won't update.

### images/

Similar to 'data.json', the folder 'images' and its content does not need to be manually created. It will generate when using the `-get_img` flag and does not need to be downloaded for future uses. Depends on 'data.json' or `-get`.

### mtg.pdf

Again, similar to 'data.json' and 'images', 'mtg.pdf' is created when using the `-pdf` flag. It contains the images of all matching cards to scale in A4 format. Depends on 'images' or `-get_img`.

### mtg.html

Similar to 'mtg.pdf' it is generated by `-html` and contains images of all cards with links to scryfall. Depends on `-get`.
