PYTHON ?= python3

.PHONY: sync sync-no-git init-git check kb-capture kb-sync kb-capture-sync

sync:
	$(PYTHON) sync_bookmarks.py

sync-no-git:
	$(PYTHON) sync_bookmarks.py --no-git

init-git:
	git init
	git add .
	git commit -m "chore: bootstrap x bookmarks sync tool"

check:
	PYTHONPYCACHEPREFIX=.pycache $(PYTHON) -m py_compile sync_bookmarks.py x_links_to_kb.py

kb-capture:
	@if [ -z "$(TEXT)" ]; then echo "Usage: make kb-capture TEXT='https://x.com/.../status/123'"; exit 1; fi
	$(PYTHON) x_links_to_kb.py capture --text "$(TEXT)"

kb-sync:
	$(PYTHON) x_links_to_kb.py sync

kb-capture-sync:
	@if [ -z "$(TEXT)" ]; then echo "Usage: make kb-capture-sync TEXT='https://x.com/.../status/123'"; exit 1; fi
	$(PYTHON) x_links_to_kb.py capture-sync --text "$(TEXT)"
