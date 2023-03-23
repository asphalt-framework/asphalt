#!/usr/bin/env python3
import importlib.metadata

from packaging.version import parse

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
]

templates_path = ["_templates"]
source_suffix = ".rst"
master_doc = "index"
project = "The Asphalt Framework (core)"
author = "Alex Grönholm"
copyright = "2015, " + author

v = parse(importlib.metadata.version("asphalt"))
version = v.base_version
release = v.public

language = "en"

exclude_patterns = ["_build"]
pygments_style = "sphinx"
highlight_language = "python3"
todo_include_todos = False

html_theme = "sphinx_rtd_theme"
htmlhelp_basename = "asphaltdoc"

intersphinx_mapping = {"python": ("https://docs.python.org/3/", None)}
