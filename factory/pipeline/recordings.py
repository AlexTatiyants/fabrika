"""A browser recording, unpacked into something a page can step through.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Callable

from .text import _first_lines


#: Steps a person did not write and does not want stepped through. A trace
#: records the harness standing the browser up as faithfully as it records the
#: test, and a walkthrough that opens on "Fixture \"browser\"" has spent the
#: reader's first three clicks before anything about the feature happens.
_TRACE_PLUMBING = ("Before Hooks", "After Hooks", "Launch browser", "Create context",
                   "Create page", "Close context", "Fixture ",
                   # Resolving a locator is not a thing a person did. It sits
                   # between the two clicks they did do, and labelling a frame
                   # with it describes the machinery rather than the work.
                   "Query count",
                   # And the settle a test ends on. The brief asks for it so
                   # the last assertion's paint lands inside the recording;
                   # labelling that final frame "Wait for timeout" would name
                   # the mechanism the frame exists because of, in the one
                   # place a reader most wants the step that mattered.
                   "Wait for timeout")


#: Frames kept per recording. Thirteen is the observed count for a short test;
#: the cap is for a long one, and it subsamples rather than truncating -- half a
#: walkthrough that stops in the middle is worse than a coarser whole one.
_TRACE_FRAMES_MAX = 60


#: Below this many bytes per pixel, a frame is one flat colour.
#:
#: The screencast starts when the browser context opens, which is before
#: anything is navigated to -- so every recording begins on two to four frames
#: of empty browser, and without a cut the player opens on them.
#:
#: A ratio is the right shape for the test: a JPEG's size per pixel measures
#: how much was drawn, and flat has a floor that holds across sizes. Measured
#: on real recordings, per pixel of the frame's own area:
#:
#:     frame            800x450          1280x720
#:     flat             .00683           .00617
#:     thinnest drawn   .01350           .02737
#:
#: Both resolutions occur: Playwright caps its trace screencast at 800 wide,
#: and a project that also records video at the viewport gets the larger
#: frames, because the two share one stream. Flat sits near .0065 either way,
#: the thinnest thing anybody drew is at least twice that, and the cut goes
#: between them.
_TRACE_BLANK_BYTES_PER_PIXEL = 0.010


def _jpeg_size(body: bytes) -> tuple[int, int]:
    """A frame's real dimensions, read off its own header.

    Not the ones the trace event declares. Those describe the page, and the
    screencast scales: with video off Playwright caps its frames at 800 wide
    while still reporting a 1280x720 page, so a ratio computed from the
    declared numbers is wrong by the square of the scale -- and a cut
    calibrated against it passes every test and then stops dropping blank
    frames the moment the frames get bigger.
    """
    i = 2
    while i + 9 < len(body):
        if body[i] != 0xFF:
            i += 1
            continue
        marker = body[i + 1]
        # Start of frame: baseline and progressive both carry the size here.
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            height, width = struct.unpack(">HH", body[i + 5:i + 9])
            return width, height
        # The markers that carry no length: padding, start of image, restarts.
        if marker in (0x01, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + struct.unpack(">H", body[i + 2:i + 4])[0]
    return 0, 0


def _without_leading_blanks(
    kept: list[tuple[dict[str, Any], str, int]],
    read: Callable[[str], bytes],
) -> list[tuple[dict[str, Any], str, int]]:
    """The empty browser a recording opens on, dropped.

    From the head only, and that restriction is the point rather than a
    simplification: a blank frame in the middle of a run is a screen that went
    white while the test was working, which is a finding and not noise.
    Dropping it would hide the most interesting thing a recording can show.

    A recording blank the whole way through keeps every frame. It has nothing
    to show, and saying so with an empty player -- which is what an unreadable
    archive says -- would lose the difference between the two.

    This reads the frames it judges, which is why it stops at the first one
    that is not flat: a long recording pays for one extra read, not for all of
    them.
    """
    flat = 0
    for _frame, hit, size in kept:
        width, height = _jpeg_size(read(hit))
        # A frame whose header will not parse cannot be judged by a ratio, so
        # it is kept and the trim stops there.
        if not (width and height):
            break
        if size >= width * height * _TRACE_BLANK_BYTES_PER_PIXEL:
            break
        flat += 1
    return kept[flat:] or kept


def trace_player(archive: Path) -> tuple[list[tuple[str, bytes]], dict[str, Any]]:
    """A recording, unpacked into something a page can step through.

    The full viewer is a service-worker application that reads the zip itself,
    which is why it cannot run inside another page and why, without this, the
    only way into one is a download and a command. What a reader wants most of
    the time is smaller than that: the frames, in order, with the name of the
    step each one belongs to -- and those are two lists in the archive already,
    on one clock.

    Nothing is read out of the zip by a path the zip chose. Frames are written
    under names this function makes up, so an archive naming
    `../../etc/anything` writes nothing anywhere.
    """
    import zipfile

    frames: list[dict[str, Any]] = []
    steps: list[tuple[float, str]] = []
    title = ""
    # What stopped the test, in the runner's own words. A test that dies in a
    # fixture before its page loads leaves one white frame and nothing else, and
    # a card showing a white frame says nothing -- one run showed fourteen of
    # them, and the reason every one was blank (`connect ECONNREFUSED
    # 127.0.0.1:8300`) was inside each archive where nobody could see it.
    error = ""
    out: list[tuple[str, bytes]] = []
    manifest: list[dict[str, Any]] = []
    shape = (0, 0)
    try:
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            # Steps from the test's own trace where there is one, frames from
            # wherever they are. Both files record the same calls -- the
            # library's and the runner's view of them, a millisecond apart --
            # so reading steps from both lists every action twice, and the
            # runner's are the ones with a name a person recognises: `Fill
            # "oracle-tag"` rather than a method and a selector.
            titled = "test.trace" if "test.trace" in names else ""
            for name in names:
                if not name.endswith(".trace"):
                    continue
                if titled and name != titled and name.endswith(".trace"):
                    # Still read it -- the frames are in here.
                    pass
                for raw in zf.read(name).decode("utf-8", "replace").splitlines():
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    kind = event.get("type")
                    if kind == "error" and event.get("message") and not error:
                        error = _first_lines(str(event["message"]), 2)
                    if kind == "context-options" and event.get("title"):
                        title = title or str(event["title"])
                    elif kind == "screencast-frame" and event.get("file"):
                        frames.append(event)
                    elif (kind == "before" and event.get("title")
                          and (not titled or name == titled)):
                        steps.append((float(event.get("startTime") or 0),
                                      str(event["title"])))
            frames.sort(key=lambda f: float(f.get("timestamp") or 0))
            steps = sorted(
                (t, label) for t, label in steps
                if not any(label.startswith(p) for p in _TRACE_PLUMBING))

            # Each frame paired with the member that holds it and that
            # member's size, both taken from the listing -- so the head can be
            # trimmed and the rest subsampled before anything is read.
            kept: list[tuple[dict[str, Any], str, int]] = []
            for frame in frames:
                member = str(frame.get("file") or "")
                # Matched against the archive's own listing rather than joined
                # to a directory, so nothing is resolved out of it.
                hit = next((n for n in names
                            if n == member or n.endswith("/" + member)), "")
                if hit:
                    kept.append((frame, hit, zf.getinfo(hit).file_size))

            # Before the cap, not after: the frames budget should be spent on
            # the part of the run that has something on it.
            kept = _without_leading_blanks(kept, zf.read)
            # Evenly, when there are more than we keep: a walkthrough that
            # stops halfway through the test is worse than a coarser one that
            # reaches the end.
            if len(kept) > _TRACE_FRAMES_MAX:
                step = len(kept) / _TRACE_FRAMES_MAX
                kept = [kept[int(i * step)] for i in range(_TRACE_FRAMES_MAX)]

            for i, (frame, hit, _size) in enumerate(kept, start=1):
                at = float(frame.get("timestamp") or 0)
                label = ""
                for when, said in steps:
                    if when <= at:
                        label = said
                    else:
                        break
                name = f"f{i:04d}.jpeg"
                body = zf.read(hit)
                # The size of the frames, off the first one that will say. The
                # event's own numbers describe the page, and the screencast is
                # free to scale below it -- so a page claiming 1280x720 while
                # every frame it serves is 800 wide is the reading a player
                # would lay out against.
                if shape == (0, 0):
                    shape = _jpeg_size(body)
                out.append((name, body))
                manifest.append({"file": name, "at": round(at, 3), "step": label})
    except (OSError, zipfile.BadZipFile, KeyError):
        return [], {}
    if not out and not (title or error):
        return [], {}
    # Said even with no frames: a recording with nothing to show still has a
    # name, which is what ties it to a criterion, and a reason.
    return out, {
        "title": title,
        "width": shape[0],
        "height": shape[1],
        "frames": manifest,
        "steps": [said for _, said in steps],
        "error": error,
    }
