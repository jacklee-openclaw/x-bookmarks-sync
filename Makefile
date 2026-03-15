PYTHON ?= python3

.PHONY: sync sync-no-git init-git check

sync:
	$(PYTHON) sync_bookmarks.py

sync-no-git:
	$(PYTHON) sync_bookmarks.py --no-git

init-git:
	git init
	git add .
	git commit -m "chore: bootstrap x bookmarks sync tool"

check:
	PYTHONPYCACHEPREFIX=.pycache $(PYTHON) -m py_compile sync_bookmarks.py
