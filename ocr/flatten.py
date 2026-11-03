# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Flattening of a receipt that is curled or folded, for the picture the user sees.

A thermal receipt curls with humidity and often arrives folded in two: its
lines of text bow, or change their slope from one half to the other. The
reading does not suffer (PP-OCR reads a bowed line), but the picture shown
next to the fields does. Flattening makes the lines straight again.

The lines are found in the picture itself, not in the words read: PP-OCR
gives one box for a whole line, which hides how the line bends. The text of
the receipt is merged into spans (one per line), each span gives a series of
points along its centre, and a single model explains the points of every
line at once::

    y = r(line) + a2 u^2 + a3 u^3 + u t(y)       u: position across the width

``r`` is the height the line has once flat; ``a2``, ``a3`` the bow of the
sheet, the same for every line (a sheet curled around a vertical axis);
``t`` the slope of the lines, which varies with the height: a perspective, a
fold (the slope changes at the crease), a sheet turned on itself. The model
is linear in its unknowns, solved by least squares, reweighted so that a
logo or a stamp does not count.

The picture is changed only when the model straightens the lines far more
than it moves them: a receipt that is already flat, a photo with too little
text, or a doubtful fit come out untouched. It is applied once: a flattened
picture flattened again does not change.
"""
import logging

from .preprocess import cv2, np

_logger = logging.getLogger(__name__)

#: Working size of the analysis (long side, pixels).
WORK_SIDE = 1400
#: Spans narrower than this share of the picture width say nothing about a bow.
MIN_SPAN_WIDTH_RATIO = 0.10
#: Fewest lines needed to trust a fit.
MIN_LINES = 7
#: A correction below this share of the picture height is not worth a resampling.
MIN_DISPLACEMENT_RATIO = 0.006
#: Above this share, the fit is more likely wrong than the sheet that curled.
MAX_DISPLACEMENT_RATIO = 0.07
#: The lines must come out at most this fraction as crooked as they went in.
MAX_RESIDUAL_RATIO = 0.8
#: The rows of ink must get at least this much crisper, or the picture is left alone.
MIN_SHARPNESS_GAIN = 1.04
#: Nodes of the slope along the height of the picture.
SLOPE_NODES = 12
#: Weight of the smoothness of the slope against the fit (per sample).
SLOPE_SMOOTHNESS = 0.03


def available():
    return cv2 is not None and np is not None


def _spans(gray):
    """Lines of text as series of points ``[(x, y)]``, in the coordinates of ``gray``."""
    height, width = gray.shape[:2]
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 25, 15)
    # The words of a line merge into one span, as far as the gap between a label and its amount
    # allows; the vertical kernel stays thin so that two lines do not.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(width // 9, 9), 3))
    merged = cv2.dilate(binary, kernel, iterations=1)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(contour) for contour in contours]
    wide = [box for box in boxes if box[2] >= MIN_SPAN_WIDTH_RATIO * width and box[3] >= 4]
    if not wide:
        return []
    typical = float(np.median([box[3] for box in wide]))
    spans = []
    for contour, (x, y, w, h) in zip(contours, boxes):
        # A span two lines tall is a merged paragraph or a barcode, not a line.
        if w < MIN_SPAN_WIDTH_RATIO * width or h < 4 or h > 2.2 * typical + 4:
            continue
        mask = np.zeros((h, w), np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, -1, offset=(-x, -y))
        # Centre of the span, column by column, over the print itself.
        ink = binary[y:y + h, x:x + w] & mask
        rows = np.arange(h, dtype=np.float64)[:, None]
        weight = (ink > 0).sum(axis=0).astype(np.float64)
        columns = np.nonzero(weight >= 1)[0]
        if len(columns) < 0.5 * w:
            continue
        centre = (rows * (ink > 0)).sum(axis=0)[columns] / weight[columns]
        step = max(len(columns) // 60, 1)
        columns, centre = columns[::step], centre[::step]
        # A median over a few points: a letter with a tail (g, p, y) would pull its own.
        padded = np.concatenate([centre[:2][::-1], centre, centre[-2:][::-1]])
        centre = np.median(np.lib.stride_tricks.sliding_window_view(padded, 5), axis=1)
        points = np.column_stack([x + columns, y + centre])
        spans.append(points)
    return spans


def _basis(u, y, nodes_y):
    """Columns of the model for points at ``u`` across and ``y`` down: bow (2) and slope nodes."""
    u = np.asarray(u, dtype=np.float64)
    hat = np.zeros((len(u), len(nodes_y)))
    step = nodes_y[1] - nodes_y[0]
    for index, node in enumerate(nodes_y):
        hat[:, index] = np.clip(1.0 - np.abs(y - node) / step, 0.0, 1.0)
    return np.column_stack([u ** 2, u ** 3]), hat * u[:, None]


def _row_sharpness(gray):
    """How crisply the ink falls into rows: variance of the row sums of the print, per row."""
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 25, 15)
    sums = (binary > 0).sum(axis=1).astype(np.float64)
    mean = sums.mean()
    return float(((sums - mean) ** 2).mean() / (mean * mean + 1e-9))


def fit(spans, width, height):
    """The model of the lines, or ``None`` when the sheet shows nothing to correct.

    Returns ``{'bow': (a2, a3), 'slope': nodes, 'nodes_y': nodes, 'before': .., 'after': ..,
    'displacement': ..}`` in the coordinates of the picture analysed.
    """
    lines = [span for span in spans if len(span) >= 8]
    if len(lines) < MIN_LINES:
        return None
    centre_x = width / 2.0
    nodes_y = np.linspace(0.0, float(height), SLOPE_NODES)
    ys = [float(np.mean(span[:, 1])) for span in lines]
    # Normalised unknowns: the line heights, the bow, the slope nodes.
    count = sum(len(span) for span in lines)
    lines_n, nodes_n = len(lines), len(nodes_y)
    u_all = np.empty(count)
    y_all = np.empty(count)
    line_of = np.empty(count, dtype=int)
    cursor = 0
    for index, span in enumerate(lines):
        n = len(span)
        u_all[cursor:cursor + n] = (span[:, 0] - centre_x) / (width / 2.0)
        y_all[cursor:cursor + n] = span[:, 1]
        line_of[cursor:cursor + n] = index
        cursor += n
    mean_y_of_sample = np.array([ys[i] for i in line_of])
    bow, slope = _basis(u_all, mean_y_of_sample, nodes_y)

    def design(use_bow, use_slope):
        columns = [np.eye(lines_n)[line_of]]
        if use_bow:
            columns.append(bow)
        if use_slope:
            columns.append(slope)
        return np.hstack(columns)

    def solve(use_bow, use_slope):
        matrix = design(use_bow, use_slope)
        # Smoothness of the slope: second difference of the nodes, per sample.
        rows = [matrix]
        targets = [y_all]
        if use_slope:
            offset = lines_n + (2 if use_bow else 0)
            smooth = np.zeros((nodes_n - 2, matrix.shape[1]))
            for k in range(nodes_n - 2):
                smooth[k, offset + k:offset + k + 3] = (1.0, -2.0, 1.0)
            factor = SLOPE_SMOOTHNESS * np.sqrt(count / max(nodes_n, 1))
            rows.append(smooth * factor)
            targets.append(np.zeros(nodes_n - 2))
        weights = np.ones(count)
        solution = None
        for _round in range(6):
            sw = np.sqrt(weights)
            a = np.vstack([matrix * sw[:, None]] + [r for r in rows[1:]])
            b = np.concatenate([y_all * sw] + [t for t in targets[1:]])
            solution, *_ = np.linalg.lstsq(a, b, rcond=None)
            residual = y_all - matrix @ solution
            scale = 1.4826 * np.median(np.abs(residual - np.median(residual))) + 0.05
            weights = np.where(np.abs(residual) <= 1.5 * scale, 1.0,
                               1.5 * scale / np.maximum(np.abs(residual), 1e-9))
        return solution, y_all - matrix @ solution, weights

    base_solution, base_residual, base_weights = solve(False, False)
    full_solution, full_residual, full_weights = solve(True, True)
    # The slope belongs to the height a line has once flat, not to the one it had in the photo: the
    # nodes are placed again with the heights the first fit found, and the fit redone.
    flat_heights = full_solution[:lines_n]
    bow, slope = _basis(u_all, flat_heights[line_of], nodes_y)
    full_solution, full_residual, full_weights = solve(True, True)

    def rms(residual, weights):
        return float(np.sqrt(np.sum(weights * residual ** 2) / max(np.sum(weights), 1.0)))

    # Both judged on the samples the full model keeps: a line the plain model cannot follow would
    # otherwise be given up as an outlier and make the plain model look good.
    before = rms(base_residual, full_weights)
    after = rms(full_residual, full_weights)
    a2, a3 = full_solution[lines_n:lines_n + 2]
    tau = full_solution[lines_n + 2:]
    grid_x = np.linspace(-1.0, 1.0, 21)
    grid_y = np.linspace(0.0, float(height), 41)
    bow_part = a2 * grid_x ** 2 + a3 * grid_x ** 3
    slope_part = np.interp(grid_y, nodes_y, tau)
    shift = bow_part[None, :] + grid_x[None, :] * slope_part[:, None]
    # The shift is judged relative to its own mean per line: a constant is no distortion.
    displacement = float(np.max(np.abs(shift - shift.mean(axis=1, keepdims=True))))
    return {
        'bow': (float(a2), float(a3)), 'tau': tau.copy(), 'nodes_y': nodes_y,
        'before': before, 'after': after, 'displacement': displacement,
        'lines': lines_n, 'width': width, 'height': height,
    }


def worth_applying(model):
    """Whether the model straightens the lines enough, and not too hard, to be applied."""
    if not model:
        return False
    height = model['height']
    if model['displacement'] < MIN_DISPLACEMENT_RATIO * height:
        return False
    if model['displacement'] > MAX_DISPLACEMENT_RATIO * height:
        return False
    return model['before'] > 0 and model['after'] <= MAX_RESIDUAL_RATIO * model['before']


def _maps(model, size, scale):
    """Sampling maps for the picture of ``size`` (width, height): ``(map_x, map_y)`` float32."""
    width, height = size
    a2, a3 = model['bow']
    tau, nodes_y = model['tau'], model['nodes_y']
    xs = np.arange(width, dtype=np.float64)
    ys = np.arange(height, dtype=np.float64)
    u = (xs * scale - model['width'] / 2.0) / (model['width'] / 2.0)
    slope = np.interp(ys * scale, nodes_y, tau)
    shift_work = (a2 * u ** 2 + a3 * u ** 3)[None, :] + u[None, :] * slope[:, None]
    # Shifts are fitted on the analysed picture: back to the real size.
    shift = shift_work / scale
    # A line is at y + shift in the photo: the flat picture takes it from there. The mean of the
    # shift over each row stays, so that the picture is not moved as a whole.
    shift = shift - shift.mean(axis=1, keepdims=True)
    map_x = np.tile(xs.astype(np.float32), (height, 1))
    map_y = (ys[:, None] + shift).astype(np.float32)
    return map_x, map_y


def flatten(image):
    """The receipt with its lines straight, and what was done; ``(image, info)``.

    ``info`` is ``None`` when the picture is left as it is (flat already, too little text,
    doubtful fit), otherwise a dictionary describing the fit (for the log and the tests).
    """
    if not available() or not hasattr(image, 'shape') or image.size == 0 or image.ndim < 2:
        return image, None
    height, width = image.shape[:2]
    scale = min(1.0, WORK_SIDE / float(max(height, width)))
    small = image if scale == 1.0 else cv2.resize(
        image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
    try:
        model = fit(_spans(gray), gray.shape[1], gray.shape[0])
    except Exception:  # noqa: BLE001 - a picture left alone is better than a failed scan
        _logger.warning("Flattening failed", exc_info=True)
        return image, None
    if not worth_applying(model):
        return image, None
    map_x, map_y = _maps(model, (width, height), scale)
    flat = cv2.remap(image, map_x, map_y, interpolation=cv2.INTER_CUBIC,
                     borderMode=cv2.BORDER_REPLICATE)
    # The check on the pixels themselves, whatever the model says: lines that are straight put the
    # ink in fewer, denser rows.
    small_flat = cv2.resize(flat, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_AREA)
    gray_flat = cv2.cvtColor(small_flat, cv2.COLOR_BGR2GRAY) if small_flat.ndim == 3 else small_flat
    before, after = _row_sharpness(gray), _row_sharpness(gray_flat)
    model['sharpness'] = (before, after)
    if after < before * MIN_SHARPNESS_GAIN:
        return image, None
    return flat, model
