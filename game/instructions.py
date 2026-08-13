"""Pre-game instructions screen: how the chosen game is played.

Shown on the way in to a game, between the main menu and the countdown.
main.py calls show() whenever the chosen game differs from the one just
played, so the page appears when a game is picked from the main menu but not
when "Restart" replays the same game -- nobody wants to re-read the rules
between rounds.

Content is per-game and lives with that game's entry in main.py's GAMES, so
adding a game stays the single edit there that main.py's docstring promises.
An entry carries the text to read, the Graphics/ art to show with it, and
how the player continues:

    Instructions(lines, images, animations, advance, advance_hint)

`advance` is a predicate (controller) -> bool polled once per frame, for
continuing with the same hardware the game itself is played with. The quiz
passes digit(2) -- the shaka -- because a quiz player is holding the
finger_digits board. Snake and minesweeper have no gesture or keyword assigned
for this yet and pass None, so they continue from the keyboard only.

ENTER / SPACE always continues and ESC always quits, on every page, so the
instructions stay usable with no board connected (see main.py, which carries on
with keyboard input only when no port opens).

One window, one screen model: this draws into the surface main.py created and
never calls pygame.display.set_mode(). See ui.py.
"""

from collections import namedtuple

import av
import pygame
from PIL import Image, ImageSequence

import ui

# Every instruction image is scaled to this height, keeping its aspect ratio,
# and laid out in a single centred row. The window is 1026x1026 (snake's grid),
# so a row of five of these fits comfortably.
IMAGE_HEIGHT = 130

# Animations get their own, taller row. A clip is the thing on the page a player
# actually studies -- someone else's hands doing what they are about to do -- so
# it is sized as the centrepiece rather than as an illustration, and a page using
# one generally drops its image row (see main.py).
#
# This is the ceiling for the pages we have: three paragraphs of text end around
# y=480 and the hint's top edge is at about 943, so the band left for art is a bit
# over 400px. Going higher does not raise the clip -- show() clamps it clear of
# the text -- it pushes it down over the hint. Fewer lines of text would buy room.
ANIM_HEIGHT = 400

# Horizontal gap between images in a row.
IMAGE_GAP = 30

# Vertical gap between the animation row and the image row.
ROW_GAP = 30

# A GIF frame may declare a delay of 0 ("as fast as the viewer can"), which
# would make a frame flash by in a single tick or, if every frame did it, make
# the loop zero-length. Treat anything shorter than this as this.
MIN_FRAME_MS = 20

# How many video frames one draw may decode to catch up with the clock. A draw
# normally needs one; the cap keeps a hitch (the first draw, a slow machine) from
# spending an unbounded time decoding frames nobody will see. The clip then lags
# behind the clock rather than fast-forwarding through it.
MAX_DECODES_PER_DRAW = 4

# What one game's instructions page shows and how it is dismissed.
#
# @param lines         paragraphs of body text, drawn top to bottom and wrapped
#                      to the window; keep them short.
# @param images        Graphics/<name>.png basenames, drawn in one row.
# @param animations    Graphics/ clips *with* their extension (.gif, .mp4, .mov),
#                      looping in their own row above the images.
# @param advance       predicate (controller) -> bool, or None for keyboard
#                      only. See digit().
# @param advance_hint  the bottom line describing how to continue. Defaults to
#                      the keyboard-only wording when empty.
Instructions = namedtuple(
    "Instructions", "lines images animations advance advance_hint",
    defaults=((), (), (), None, ""),
)

_images = {}
_anims = {}


def _scale(img, height):
    """Return `img` scaled to about `height`, keeping its aspect ratio.

    Two kinds of art share the Graphics/ folder and they need opposite
    treatment. The hand photos are large (up to 800x560) and get scaled down,
    where smoothscale's interpolation is what you want. Snake's heads are 40x40
    pixel art drawn for a 40px cell, so reaching IMAGE_HEIGHT means enlarging
    them 3x -- and smoothscale renders that as a blur. Those are enlarged by a
    whole number with nearest-neighbour instead, which keeps the pixels crisp;
    the result is a little shorter than asked for (120px at 3x), which is
    invisible in a row centred on one line.
    """
    w, h = img.get_size()
    factor = height / h
    if factor > 1:
        whole = max(1, int(factor))
        return pygame.transform.scale(img, (w * whole, h * whole))
    return pygame.transform.smoothscale(img, (round(w * factor), height))


def _load(name):
    """Return Graphics/<name>.png scaled to about IMAGE_HEIGHT, loading it once.

    Cached because show() runs on every entry to a game and decoding a PNG per
    visit is pointless. Paths are relative to game/, like the fonts in ui.py.
    """
    if name not in _images:
        img = pygame.image.load(f"Graphics/{name}.png").convert_alpha()
        _images[name] = _scale(img, IMAGE_HEIGHT)
    return _images[name]


class _Gif:
    """A GIF decoded up front into scaled surfaces, ready to loop.

    pygame cannot animate a GIF: image.load() hands back the first frame and
    nothing else. So Pillow decodes it and each frame is handed to pygame as raw
    RGBA. Iterating with ImageSequence composites the frames for us, so a GIF
    whose later frames only store the pixels that changed still yields whole
    pictures rather than fragments on a transparent field.

    Decoding up front is affordable here because the GIFs on these pages are a
    handful of frames. Video is not -- see _Video.
    """

    def __init__(self, path, height):
        frames = []
        with Image.open(path) as gif:
            for frame in ImageSequence.Iterator(gif):
                rgba = frame.convert("RGBA")
                surf = pygame.image.frombytes(
                    rgba.tobytes(), rgba.size, "RGBA").convert_alpha()
                delay = max(MIN_FRAME_MS, frame.info.get("duration", 100))
                frames.append((_scale(surf, height), delay))
        self._frames = tuple(frames)
        self._epoch = 0

    def rewind(self, now):
        """Restart the loop as of `now` ms."""
        self._epoch = now

    def surface(self, now):
        """The frame showing at `now` ms."""
        t = (now - self._epoch) % sum(delay for _, delay in self._frames)
        for surf, delay in self._frames:
            if t < delay:
                return surf
            t -= delay
        return self._frames[-1][0]


class _Video:
    """A video file decoded frame by frame as the page draws, looping forever.

    pygame has no video support at all (pygame.movie went away with pygame 1), so
    PyAV -- ffmpeg's decoders behind a Python API -- does the decoding and each
    frame becomes a surface here.

    Streamed rather than unpacked into surfaces up front, unlike _Gif: a 6 s clip
    at 30 fps is 180 frames, which at ANIM_HEIGHT would be some 90 MB of surfaces
    and a visible stall on opening the page. This way memory stays flat at one
    frame and the page appears immediately.

    Frames are chosen by wall clock rather than by counting draws, so the clip
    runs at its true speed no matter what the draw loop is doing, and scaling
    happens once per *video* frame instead of once per draw -- at 30 fps against a
    60 fps loop, half the work.
    """

    def __init__(self, path, height):
        self._container = av.open(path)
        self._stream = self._container.streams.video[0]
        self._stream.thread_type = "AUTO"

        # Display size, fixed once: ffmpeg scales to it while it converts colour
        # (see _to_surface), so the clip is never scaled twice or carried around
        # at source resolution.
        src = self._stream.codec_context
        self._size = (round(src.width * height / src.height), height)

        self._surf = None
        self.rewind(0)
        if self._surf is None:
            raise ValueError(f"{path}: ingen bilder i videoen")

    def rewind(self, now):
        """Seek back to the first frame and play from `now` ms."""
        self._container.seek(0)
        self._decoder = self._container.decode(self._stream)
        self._pending = next(self._decoder, None)
        self._epoch = now
        self._advance(now)

    def _to_surface(self, frame):
        """`frame` as a page-sized surface.

        One reformat does the scaling and the conversion to RGB together, which
        beats converting at source size and then calling _scale: it is a single
        pass in ffmpeg's scaler and the buffer that comes out is display-sized
        (0.7 MB here) instead of full-frame (1.3 MB), and that allocation happens
        30 times a second.

        The bytes are taken from the plane rather than through numpy (not a
        dependency here) or Pillow (6.9 ms a frame, against 2.7 for this).
        ffmpeg pads each row out to an alignment boundary, so the surface is
        created at the padded width and then cropped back.
        """
        rgb = frame.reformat(width=self._size[0], height=self._size[1],
                             format="rgb24")
        plane = rgb.planes[0]
        surf = pygame.image.frombuffer(
            bytes(plane), (plane.line_size // 3, rgb.height), "RGB")
        if surf.get_width() != rgb.width:
            surf = surf.subsurface((0, 0, rgb.width, rgb.height))
        return surf

    def _advance(self, now):
        """Decode up to the frame due at `now`. False once the clip has ended."""
        for _ in range(MAX_DECODES_PER_DRAW):
            if self._pending is None:
                return False
            elapsed = (now - self._epoch) / 1000
            if self._pending.time is not None and self._pending.time > elapsed:
                break
            self._surf = self._to_surface(self._pending)
            self._pending = next(self._decoder, None)
        return True

    def surface(self, now):
        """The frame showing at `now` ms, restarting the clip when it runs out."""
        if not self._advance(now):
            self.rewind(now)
        return self._surf


def _load_anim(name):
    """Return the looping clip Graphics/<name>, opening it once.

    `name` carries its extension because the two kinds are handled by entirely
    different machinery -- Pillow decodes a GIF up front, PyAV streams a video --
    but both answer rewind(now) and surface(now), so show() never asks which it
    got.
    """
    if name not in _anims:
        path = f"Graphics/{name}"
        suffix = name.rsplit(".", 1)[-1].lower()
        if suffix == "gif":
            _anims[name] = _Gif(path, ANIM_HEIGHT)
        elif suffix in ("mp4", "mov", "m4v"):
            _anims[name] = _Video(path, ANIM_HEIGHT)
        else:
            raise ValueError(f"instructions: ukjent klippformat: {name}")
    return _anims[name]


def _draw_row(screen, surfaces, cx, y):
    """Blit `surfaces` as one row, centred on x=cx and vertically on y."""
    total_w = (sum(s.get_width() for s in surfaces)
               + IMAGE_GAP * (len(surfaces) - 1))
    x = cx - total_w // 2
    for surf in surfaces:
        screen.blit(surf, surf.get_rect(midleft=(x, y)))
        x += surf.get_width() + IMAGE_GAP


def digit(n):
    """Build an `advance` predicate that fires when the player shows `n` fingers.

    The finger_digits board only reports a digit after five identical
    predictions in a row, so one settled gesture arrives as one token -- a hand
    held up does not stream.

    The queue is emptied on every call rather than peeked at, for two reasons: a
    non-matching count (a stray 3) must not sit at the head blocking the digit
    we want, and nothing shown on this page may survive into the game and answer
    its first question.
    """
    def advance(controller):
        matched = False
        while not controller.digits.empty():
            if controller.digits.get() == n:
                matched = True
        return matched

    return advance


def show(screen, clock, controller, game):
    """Display `game`'s instructions until the player continues or quits.

    @return True to go on to the game, False if the player quit (ESC or the
            window closing). A game with no instructions returns True
            immediately without drawing anything.
    """
    guide = game.instructions
    if guide is None:
        return True

    title_font = ui.font(64)
    line_font = ui.font(30)
    hint_font = ui.font(26)

    width, height = screen.get_size()
    cx = width // 2
    margin = 60

    imgs = [_load(name) for name in guide.images]
    anims = [_load_anim(name) for name in guide.animations]
    hint_y = height - 70

    # Clips start from the top on every visit to the page: they are cached and
    # would otherwise resume wherever the last player left them.
    for anim in anims:
        anim.rewind(pygame.time.get_ticks())

    while True:
        # ---- input --------------------------------------------------------
        # Poll the continue gesture before draining: it reads the digit queue,
        # which drain() would otherwise empty first.
        if guide.advance is not None and guide.advance(controller):
            return True

        # The game's own button starts it: btn0 (SNAKE) on Snake's page, etc.
        # Its menu token matches the game name. Checked before drain(), which
        # would otherwise empty the queue.
        if controller.get_menu() == game.token:
            return True

        # Everything else -- swipes, spoken numbers, other buttons -- means
        # nothing on this page and would be stale by the time the game starts.
        controller.drain(menu=False)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    return True
                if event.key == pygame.K_ESCAPE:
                    return False

        # ---- draw ---------------------------------------------------------
        screen.fill(ui.BG_COLOR)

        title_surf = title_font.render(f"How to play: {game.name}", True, ui.TEXT_COLOR)
        screen.blit(title_surf, title_surf.get_rect(center=(cx, 100)))

        # Body text, left-aligned and wrapped, with a gap between paragraphs.
        y = 210
        for line in guide.lines:
            for wrapped in ui.wrap_text(line, line_font, width - 2 * margin):
                surf = line_font.render(wrapped, True, ui.TEXT_COLOR)
                screen.blit(surf, surf.get_rect(midleft=(margin, y)))
                y += line_font.get_linesize() + 6
            y += 16

        # Art strip: the animations on one centred row, the stills on another
        # below it. The block is centred in the band between the end of the text
        # and the top of the hint rather than pinned to the bottom, so a short
        # page doesn't leave a hole in the middle, and it never rides up into the
        # text. A block taller than the band spills over the hint -- see
        # ANIM_HEIGHT.
        rows = []
        if anims:
            now = pygame.time.get_ticks()
            rows.append(([a.surface(now) for a in anims], ANIM_HEIGHT))
        if imgs:
            rows.append((imgs, IMAGE_HEIGHT))

        block_h = sum(h for _, h in rows) + ROW_GAP * (len(rows) - 1)
        band_top = y + 20
        band_h = (hint_y - hint_font.get_linesize() // 2 - ROW_GAP) - band_top
        top = band_top + max(0, (band_h - block_h) // 2)
        for surfaces, row_h in rows:
            _draw_row(screen, surfaces, cx, top + row_h // 2)
            top += row_h + ROW_GAP

        hint = guide.advance_hint or "Trykk samme knapp igjen for å starte"
        hint_surf = hint_font.render(hint, True, ui.TEXT_COLOR)
        screen.blit(hint_surf, hint_surf.get_rect(center=(cx, hint_y)))

        pygame.display.update()
        clock.tick(60)
