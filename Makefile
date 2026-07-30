PYTHONPATH := src
PYTHON := python3
PROFILE ?= me
PROFILE_DATA := data/sources.$(PROFILE).json
PROFILE_HISTORY := data/subscription-history.$(PROFILE).json
DATA ?= $(PROFILE_DATA)
HISTORY ?= $(PROFILE_HISTORY)
IMPORT_OPML ?= imports/netnewswire.opml
EXPORT_OPML ?= exports/$(PROFILE)-netnewswire.opml
CLI := PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m netnewswire_feed_booster --data $(DATA) --history $(HISTORY)

.PHONY: help test starter-import list import export hosted-export modal-deploy publish-netnewswire unfollows history

help:
	$(CLI) --help

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests

starter-import:
	./scripts/build_starter_import.sh $(PROFILE)

list:
	$(CLI) list --profile $(PROFILE)

import:
	$(CLI) import-opml $(IMPORT_OPML) --profile $(PROFILE)

export:
	$(CLI) export-opml --profile $(PROFILE) --out $(EXPORT_OPML)

hosted-export:
	./scripts/netnewswire_workflow.sh export

modal-deploy:
	./scripts/netnewswire_workflow.sh deploy-modal

publish-netnewswire:
	./scripts/netnewswire_workflow.sh all

unfollows:
	$(CLI) unfollow-checklist --profile $(PROFILE)

history:
	$(CLI) list-history --profile $(PROFILE)
