"""Recordings of the browser runs: the list, the walkthrough's frames, and the archive."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from ..pipeline import current_recordings
from ..workspace import PathEscape, safe_join

from .context import Context


def register(app: FastAPI, ctx: Context) -> None:
    """The recordings of a feature's browser runs."""
    store_or_404 = ctx.store_or_404

    @app.get("/api/projects/{project_id}/features/{feature_id}/traces")
    def list_traces(project_id: str, feature_id: str) -> dict[str, Any]:
        """Recordings of the browser runs, and what each one costs to open.

        A trace is every moment of a test, with the DOM at each of them, and it
        is the only evidence in a packet that answers "the test says it passes
        -- show me" without asking a reader to trust that the right instant was
        frozen. A screenshot asks exactly that.

        Only the latest round's: a recording of a run against a tree three
        repairs ago describes a branch that no longer exists. Which round that
        is, is `current_recordings`' to say, and the packet asks it the same
        question -- so this list and the packet's account of it cannot differ.
        """
        store = store_or_404(project_id, feature_id)
        d = store.artifact_dir("traces")
        current = current_recordings(store.records())
        # None: recorded before recordings were tied to a round, so everything
        # on disk is all there is to go on.
        mine = (None if current is None
                else {name for p in current for name in (p.get("files") or [])})
        out: list[dict[str, Any]] = []
        for f in sorted(p for p in d.iterdir()
                        if p.is_file() and p.suffix.lower() == ".zip"):
            if mine is not None and f.name not in mine:
                continue
            try:
                stem = f.name[:-4] if f.name.endswith(".zip") else f.name
                manifest = d / f"{stem}.player.json"
                title, poster, error, shots = "", "", "", []
                if manifest.is_file():
                    try:
                        read = json.loads(manifest.read_text(encoding="utf-8"))
                        title = str(read.get("title") or "")
                        error = str(read.get("error") or "")
                        shots = read.get("frames") or []
                        # The last frame. A recording ends where the test
                        # finished, which is the state a reader is looking for
                        # when they glance at a card -- the first frame is a
                        # blank page every time.
                        poster = str((shots[-1] or {}).get("file") or "") if shots else ""
                    except (OSError, ValueError):
                        pass
                out.append({
                    "name": f.name, "bytes": f.stat().st_size,
                    # Whether there is a walkthrough, so a row can offer one
                    # rather than offering one with nothing in it.
                    "frames": bool(shots),
                    # What the recording is of, and one frame to show for it.
                    # The title carries the criterion ids a test names itself
                    # with; which of them are this feature's is the console's
                    # to decide, against the spec it is already holding.
                    "title": title, "poster": poster,
                    # What stopped the test, when something did. A card whose
                    # only frame is a white page needs the reason more than
                    # the picture.
                    "error": error,
                })
            except OSError:
                continue
        # Said once, here, rather than built into a string in the console: the
        # command is a property of the artifact and a reader who downloads one
        # needs it whatever screen they came from.
        return {"traces": out, "open_with": "npx playwright show-trace <file>"}

    @app.get("/api/projects/{project_id}/features/{feature_id}/traces/{name}/player")
    def get_trace_player(project_id: str, feature_id: str, name: str) -> dict[str, Any]:
        """The frames of one recording, in order, each named for its step.

        Unpacked when the recording was collected, because the full viewer
        cannot run inside another page -- it is a service-worker application
        that reads the archive itself -- and a download plus a command is a
        poor answer to "show me". This is the walkthrough: an image, a
        scrubber, and the name of what the test was doing at that moment.
        """
        store = store_or_404(project_id, feature_id)
        stem = name[:-4] if name.endswith(".zip") else name
        try:
            manifest = safe_join(store.artifact_dir("traces"), f"{stem}.player.json")
        except PathEscape:
            raise HTTPException(status_code=404, detail="no such recording") from None
        if not manifest.is_file():
            # The archive may still be downloadable: a recording that could not
            # be unpacked is not a recording that is missing.
            raise HTTPException(status_code=404, detail="this recording has no frames")
        try:
            return json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="this recording has no frames") from None

    @app.get("/api/projects/{project_id}/features/{feature_id}/traces/{name}/frames/{frame}")
    def get_trace_frame(project_id: str, feature_id: str, name: str,
                        frame: str) -> FileResponse:
        store = store_or_404(project_id, feature_id)
        stem = name[:-4] if name.endswith(".zip") else name
        try:
            target = safe_join(store.artifact_dir("traces"), f"{stem}.frames/{frame}")
        except PathEscape:
            raise HTTPException(status_code=404, detail="no such frame") from None
        if not target.is_file() or target.suffix.lower() not in (".jpeg", ".jpg"):
            raise HTTPException(status_code=404, detail="no such frame")
        return FileResponse(str(target), media_type="image/jpeg")

    @app.get("/api/projects/{project_id}/features/{feature_id}/traces/{name}")
    def get_trace(project_id: str, feature_id: str, name: str) -> FileResponse:
        store = store_or_404(project_id, feature_id)
        try:
            target = safe_join(store.artifact_dir("traces"), name)
        except PathEscape:
            raise HTTPException(status_code=404, detail="no such recording") from None
        if not target.is_file() or target.suffix.lower() != ".zip":
            raise HTTPException(status_code=404, detail="no such recording")
        # Downloaded, not rendered: nothing in a browser opens one of these
        # except Playwright's own viewer.
        return FileResponse(str(target), media_type="application/zip",
                            filename=target.name)
