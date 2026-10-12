"""Updating the app, and talking to the NAS updater.

A release install updates from Settings › Updates (updates.py): the backend
copies the database and leaves a request, and truenas/obsidian-updater.py,
as root on the host, moves the app to that version. A git-pull install
updates by pulling its deployed clone instead (deploy.py). channel.py is
the request-file mechanism every host-changing feature shares."""
