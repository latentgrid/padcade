# SPDX-License-Identifier: GPL-3.0-or-later
"""A Launchpad Pro on a Raspberry Pi as a pad display: pages, scenes and games as plugins, one config file, and a
management page that doubles as a simulator."""
NAME = 'Padcade'  # the one place the name lives: the page, the folders and the service take it from here
SLUG = NAME.lower().replace(' ', '-')
VERSION = '1.0.0'
API = 2  # the plugin interface's version: a plugin written for a newer one is refused
REPO = 'latentgrid/padcade'  # 'owner/name' on GitHub: where install.sh --update and the Settings screen look for releases
