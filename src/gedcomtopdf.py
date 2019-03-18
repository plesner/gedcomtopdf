#!/usr/bin/env python

import argparse
import datetime
import io
import logging
import os
import os.path
import re
import shutil

import babel.dates
import gedcom
import pdfkit
import requests
import PIL.Image

LOGGER = logging.getLogger(__name__)


class Image(object):

  def __init__(self, node, cache):
    self.node = node
    self.cache = cache
    self.path_cache = None
    self.serial = None

  @property
  def url(self):
    return self.node["FILE"].value

  @property
  def title(self):
    for elm in self.node.get_list("TITL"):
      return elm.value

  @property
  def note(self):
    note = self.node.note
    if note:
      return note.replace("\\n", "\n")
    else:
      return note

  @property
  def path(self):
    if not self.path_cache:
      self.path_cache = self.maybe_fetch()
    return self.path_cache

  NON_ASCII = re.compile("[^\w\d.]+")
  def maybe_fetch(self):
    if not os.path.exists(self.cache):
      os.makedirs(self.cache)
    filename = self.NON_ASCII.sub("_", self.url)
    cachename = os.path.join(self.cache, filename)
    if os.path.exists(cachename):
      return cachename
    LOGGER.info("Fetching %s", self.url)
    req = requests.get(self.url, stream=True)
    assert req.status_code == 200
    total_size = int(req.headers.get("Content-Length", -1))
    fetched_size = 0
    buffer = io.BytesIO()
    for chunk in req.iter_content(chunk_size=1024, decode_unicode=False):
      fetched_size += len(chunk)
      buffer.write(chunk)
    req.close()
    with open(cachename, "wb") as out:
      out.write(buffer.getvalue())
    return cachename

  def open(self):
    return PIL.Image.open(self.path)


class Date(object):

  def __init__(self, node):
    self.node = node

  @property
  def value(self):
    for d in self.node.get_list("DATE"):
      return d.value

  FORMAT = re.compile("^(?P<day>\d{1,2})?\s*(?P<month>[A-Z]{3})?\s*(?P<year>\d{4})$")
  MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
  @property
  def date(self):
    value = self.value
    if not value:
      return None
    match = self.FORMAT.match(value)
    if not match:
      raise AssertionError("Unexpected date {}".format(value))
    if match.group("day") and match.group("month"):
      return datetime.date(
        year=int(match.group("year")),
        month=self.MONTHS.index(match.group("month")) + 1,
        day=int(match.group("day")))
    elif match.group("year"):
      return datetime.date(
        year=int(match.group("year")),
        month=1,
        day=1)
    else:
      return None

  def __str__(self):
    return format_date(self.date)


class Name(object):

  def __init__(self, node):
    self.node = node

  @property
  def given(self):
    for g in self.node.get_list("GIVN"):
      return g.value

  @property
  def surname(self):
    for s in self.node.get_list("SURN"):
      return s.value

  @property
  def maiden(self):
    for m in self.node.get_list("_MARNM"):
      return m.value

  @property
  def last_name(self):
    maiden = self.maiden
    surname = self.surname
    return maiden if maiden else surname

  def __str__(self):
    given = self.given
    surname = self.surname
    maiden = self.maiden
    if maiden:
      return "{} {} (f. {})".format(given, maiden, surname)
    else:
      return "{} {}".format(given, surname)


class Individual(object):

  def __init__(self, node, tree):
    self.node = node
    self.tree = tree
    self.serial = None
    self.images = self.build_images()

  @property
  def name(self):
    for n in self.node.get_list("NAME"):
      return Name(n)

  def build_images(self):
    result = []
    for node in self.node.get_list("OBJE"):
      image = Image(node, self.tree.cache)
      if image.title:
        result.append(image)
    return result

  @property
  def birth(self):
    for b in self.node.get_list("BIRT"):
      return Date(b)

  @property
  def death(self):
    for d in self.node.get_list("DEAT"):
      return Date(d)

  @property
  def children(self):
    result = []
    for id in self.node.get_list("FAMS"):
      family = self.tree.node[id.value]
      partner_ids = set([node.value for node in family.partners])
      if not self.node.id in partner_ids:
        continue
      for child in family.get_list("CHIL"):
        result.append(self.tree.individual_by_id(child.value))
    return result

  @property
  def parents(self):
    return [self.tree.individual_by_id(p.id) for p in self.node.parents]

  @property
  def html_summary(self):
    birth = format_date(self.birth)
    death = format_date(self.death)
    name = html_escape(str(self.name))
    if self.serial:
      name = "{}<sup>{}</sup>".format(name, self.serial)
    if not birth and not death:
      data = name
    else:
      data = "{} ({}-{})".format(name, html_escape(birth), html_escape(death))
    return data


class Tree(object):

  def __init__(self, source, cache):
    self.node = gedcom.parse(io.StringIO(source))
    self.cache = cache
    self.individual_cache = {}
    self.individuals = self.build_individuals()

  def build_individuals(self):
    pairs = []
    for node in self.node.individuals:
      indiv = self.individual_by_id(node.id)
      birth = indiv.birth
      if birth:
        date = birth.date
      else:
        date = datetime.date(year=1, month=1, day=1)
      pairs.append(((indiv.name.last_name, str(indiv.name), date), indiv))
    pairs = sorted(pairs)
    result = [i for (k, i) in pairs]
    names = {}
    for entry in result:
      names.setdefault(str(entry.name), []).append(entry)
    for entries in names.values():
      if len(entries) == 1:
        continue
      for i in range(0, len(entries)):
        entries[i].serial = (i + 1)
    next_serial = 1
    for entry in result:
      for image in entry.images:
        image.serial = next_serial
        next_serial += 1
    return result

  def individual_by_id(self, id):
    result = self.individual_cache.get(id)
    if result is None:
      result = Individual(self.node[id], self)
      self.individual_cache[id] = result
    return result

  LINE_FORMAT = re.compile("^(?P<level>[0-9]) ((?P<id>@[-a-zA-Z0-9]+@) )?(?P<tag>[_A-Z0-9]+)( (?P<value>.*))?$")
  @classmethod
  def read(cls, filename, cache):
    lines = []
    with open(filename, encoding="utf-8-sig") as file:
      block = []
      def flush_block():
        if block:
          lines.append("\\n".join(block))
      for line in file.readlines():
        line = line.strip()
        if cls.LINE_FORMAT.match(line):
          flush_block()
          block = [line]
        else:
          block.append(line)
    return cls("\n".join(lines), cache)


def html_escape(s):
  if s:
    return str(s).encode("ascii", "xmlcharrefreplace").decode()
  else:
    return s


def format_date(d):
  if isinstance(d, Date):
    d = d.date
  if not d:
    return ""
  if d.month == 1 and d.day == 1:
    return str(d.year)
  else:
    return babel.dates.format_date(d, locale="da")


def individual_to_html(individual):
  info = []
  birth = format_date(individual.birth)
  if birth:
    info.append("""<div class="marker">&#x2605;</div>{}""".format(html_escape(birth)))
  death = format_date(individual.death)
  if death:
    info.append("""<div class="marker">&#x271d;</div>{}""".format(html_escape(death)))
  children = individual.children
  parents = individual.parents
  if parents:
    info.append("<h4>For&aelig;ldre</h4>{}".format(
      "<br/>".join(["""<div class="marker">&bull;</div>{}""".format(p.html_summary) for p in parents])))
  if children:
    info.append("<h4>B&oslash;rn</h4>{}".format(
      "<br/>".join(["""<div class="marker">&bull;</div>{}""".format(c.html_summary) for c in children])))
  images = individual.images
  if images:
    info.append("<h4>Billeder</h4>{}".format(
      "<br/>".join(["""<div class="marker">&bull;</div>{}: {}""".format(i.serial, i.title) for i in images])))

  return "<div><h2>{name}</h2>{info}</div>\n".format(
    name=individual.html_summary,
    info="<br/>".join(info))


def place_image(width, height, max_width, max_height):
  rotate = False
  if width > height:
    rotate = True
    (width, height) = (height, width)
  ratio = (height / width)
  if max_width * ratio <= max_height:
    return (rotate, max_width, max_width * ratio)
  else:
    return (rotate, max_height / ratio, max_height)


def images_to_html(individual):
  lines = []
  for image in individual.images:
    (width, height) = image.open().size
    radius = 6
    (rotate, width_cm, height_cm) = place_image(width, height, 21 - radius, 29.7 - radius)
    if rotate:
      klass = "imageRotate"
      imgstyle = "width: {height}cm; height: {width}cm; margin-left: {width}cm;".format(
        width=width_cm, height=height_cm)
    else:
      klass = "imagePlain"
      imgstyle = "width: {width}cm; height: {height}cm;".format(
        width=width_cm, height=height_cm)
    if not image.note:
      LOGGER.warning("Image for %s has no note", individual.name)
      continue
    lines.append("""
      <h2 style="page-break-before: always; text-align: center;">{serial}: {title}</h2>
      <div class="imgContainer" style="width: {width}cm; height: {height}cm;">
        <img class="{klass}" src="file:{path}" style="{style}"/>
      </div>
      <div class="note">{note}</div>
      """.format(
      serial=image.serial,
      path=image.path,
      klass=klass,
      width=width_cm,
      height=height_cm,
      style=imgstyle,
      title=image.title,
      note=html_escape(image.note.replace("\n", "<br/>"))))
  return "".join(lines)


def tree_to_html(tree):
  individuals = [individual_to_html(i) for i in tree.individuals]
  all_images = []
  for indiv in tree.individuals:
    for image in indiv.images:
      all_images.append(image)
  images = [images_to_html(i) for i in tree.individuals]
  html = """
<html>
  <head>
    <meta name="pdfkit-encoding" content="UTF-8"/>
    <style>
      body {{
        font-family: sans-serif;
      }}
      @media print {{
        @page {{
          size: A4 portrait;
        }}
      }}
      .imageRotate {{
        transform: rotate(90deg);
        transform-origin: top left;
      }}
      .imagePlain {{
      
      }}
      .imgContainer {{
        display: block;
        margin-left: auto;
        margin-right: auto;
        margin-top: auto;
        margin-bottom: auto;      
      }}
      .note {{
        margin-left: auto;
        margin-right: auto;
        margin-top: 0.5cm;
        width: fit-content;
        font-size: 80%;
      }}
      .marker {{
        width: 2em;
        display: inline-block;
        text-align: center;
      }}
    </style>
  </head>
  <body>
    {individuals}
    {images}
  </body>
</html>
  """.format(
    individuals="".join(individuals),
    images="".join(images))
  return (html, all_images)


def name_to_html(name):
  return html_escape(str(name))


def build_argparser():
  parser = argparse.ArgumentParser()
  parser.add_argument("file")
  parser.add_argument("--cache", default=os.path.abspath("./.image_cache"), required=False)
  parser.add_argument("--pdf", required=False)
  parser.add_argument("--html", required=False)
  parser.add_argument("--images", required=False)
  return parser


NON_ASCII = re.compile("[^\w\d]+")
TABLE = str.maketrans({"å": "aa", "ø": "oe", "æ": "ae"})
def simplify_title(input):
  return NON_ASCII.sub("-", input.lower()).translate(TABLE)

def main():
  logging.basicConfig(level="INFO")
  args = build_argparser().parse_args()
  tree = Tree.read(args.file, args.cache)
  (html, images) = tree_to_html(tree)
  if args.html:
    with open(args.html, "wt", encoding="utf-8") as file:
      file.write(html)
  if args.pdf:
    pdfkit.from_string(html, args.pdf)
  if args.images:
    os.makedirs(args.images, exist_ok=True)
    index = 1
    for image in images:
      srcpath = image.path
      (base, ext) = os.path.splitext(srcpath)
      simpletitle = simplify_title(image.title)
      destpath = os.path.join(args.images, "{:03}-{}{}".format(index, simpletitle, ext))
      index += 1
      shutil.copy(srcpath, destpath)


if __name__ == "__main__":
  main()
