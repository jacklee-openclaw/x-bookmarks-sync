PYTHON ?= python3

.PHONY: check status path enqueue sync index search list migrate migrate-apply legacy-sync legacy-sync-no-git

check:
	PYTHONPYCACHEPREFIX=.pycache $(PYTHON) -m py_compile legacy/sync_bookmarks.py x_links_to_kb.py

status:
	$(PYTHON) x_links_to_kb.py status

path:
	$(PYTHON) x_links_to_kb.py path

enqueue:
	@if [ -z "$(TEXT)" ]; then echo "Usage: make enqueue TEXT='https://x.com/.../status/123'"; exit 1; fi
	$(PYTHON) x_links_to_kb.py enqueue --text "$(TEXT)"

sync:
	$(PYTHON) x_links_to_kb.py sync

index:
	$(PYTHON) x_links_to_kb.py index

search:
	@if [ -z "$(Q)" ]; then echo "Usage: make search Q='agent workflow'"; exit 1; fi
	$(PYTHON) x_links_to_kb.py search "$(Q)"

list:
	$(PYTHON) x_links_to_kb.py list

migrate:
	$(PYTHON) x_links_to_kb.py migrate

migrate-apply:
	$(PYTHON) x_links_to_kb.py migrate --apply

legacy-sync:
	$(PYTHON) legacy/sync_bookmarks.py

legacy-sync-no-git:
	$(PYTHON) legacy/sync_bookmarks.py --no-git
