/**
 * Utility for resolving track-related assets
 */

/**
 * Returns the path to the national flag image for a given country name.
 * The country name is provided by the backend metadata.
 */
export const getCountryFlagPath = (country?: string): string => {
  if (!country) return '';
  return `/country_flag/${country}.png`;
};

// --- Corner detection for automatic turn numbering ---

export interface DetectedCorner {
  index: number;
  x: number;
  y: number;
  z: number;
  dx: number;
  dy: number;
  label: string;
}

export function detectCorners(
  points: { x: number; y: number; z?: number }[],
  options?: { smoothWindow?: number; curvatureThreshold?: number; mergeDistance?: number }
): DetectedCorner[] {
  const n = points.length;
  if (n < 20) return [];

  const smoothWindow = options?.smoothWindow ?? 12;
  const mergeDistance = options?.mergeDistance ?? 80;

  // 1. Compute heading angle at each point
  const headings: number[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const next = (i + 1) % n;
    headings[i] = Math.atan2(points[next].y - points[i].y, points[next].x - points[i].x);
  }

  // 2. Compute raw curvature (absolute heading change per step)
  const rawCurvature: number[] = new Array(n).fill(0);
  for (let i = 0; i < n; i++) {
    const prev = (i - 1 + n) % n;
    let dh = headings[i] - headings[prev];
    if (dh > Math.PI) dh -= 2 * Math.PI;
    if (dh < -Math.PI) dh += 2 * Math.PI;
    rawCurvature[i] = Math.abs(dh);
  }

  // 3. Smooth curvature with moving average
  const curvature: number[] = new Array(n).fill(0);
  for (let i = 0; i < n; i++) {
    let sum = 0;
    for (let j = -smoothWindow; j <= smoothWindow; j++) {
      sum += rawCurvature[(i + j + n) % n];
    }
    curvature[i] = sum / (2 * smoothWindow + 1);
  }

  // 4. Adaptive threshold via percentile (top ~25% of curvature = corners)
  const sorted = curvature.filter(c => c > 1e-6).sort((a, b) => a - b);
  const percentile75 = sorted.length > 0 ? sorted[Math.floor(sorted.length * 0.75)] : 0;
  const threshold = options?.curvatureThreshold ?? percentile75;

  if (threshold <= 0) return [];

  // 5. Find contiguous high-curvature regions and pick peak within each
  const peaks: { index: number; value: number }[] = [];
  let inRegion = false;
  let regionStart = -1;
  let regionPeak = 0;
  let regionPeakIdx = 0;

  // Handle wrap-around: scan n + smoothWindow points
  for (let i = 0; i < n + smoothWindow * 2; i++) {
    const idx = i % n;
    if (curvature[idx] >= threshold) {
      if (!inRegion) {
        inRegion = true;
        regionStart = i;
        regionPeak = curvature[idx];
        regionPeakIdx = idx;
      } else if (curvature[idx] > regionPeak) {
        regionPeak = curvature[idx];
        regionPeakIdx = idx;
      }
    } else if (inRegion) {
      inRegion = false;
      peaks.push({ index: regionPeakIdx, value: regionPeak });
    }
  }
  if (inRegion) {
    peaks.push({ index: regionPeakIdx, value: regionPeak });
  }

  // 6. Deduplicate peaks (same index from wrap-around)
  const seen = new Set<number>();
  const uniquePeaks = peaks.filter(p => {
    if (seen.has(p.index)) return false;
    seen.add(p.index);
    return true;
  });

  // 7. Merge peaks that are too close (distance in meters between points)
  uniquePeaks.sort((a, b) => a.index - b.index);
  const merged: { index: number; value: number }[] = [];
  for (const peak of uniquePeaks) {
    if (merged.length === 0) {
      merged.push(peak);
      continue;
    }
    const last = merged[merged.length - 1];
    const p1 = points[last.index];
    const p2 = points[peak.index];
    const dist = Math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2);
    if (dist < mergeDistance) {
      // Keep the stronger peak
      if (peak.value > last.value) {
        merged[merged.length - 1] = peak;
      }
    } else {
      merged.push(peak);
    }
  }

  // 8. Also merge first and last if they're close (circuit wrap)
  if (merged.length >= 2) {
    const first = merged[0];
    const last = merged[merged.length - 1];
    const p1 = points[first.index];
    const p2 = points[last.index];
    const dist = Math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2);
    if (dist < mergeDistance) {
      if (first.value >= last.value) {
        merged.pop();
      } else {
        merged.shift();
      }
    }
  }

  // 9. Sort by track position (index) and number sequentially
  merged.sort((a, b) => a.index - b.index);

  return merged.map((peak, i) => {
    const p = points[peak.index];
    const prev = points[(peak.index - 8 + n) % n];
    const next = points[(peak.index + 8) % n];
    const lx = next.x - prev.x;
    const ly = next.y - prev.y;
    const len = Math.sqrt(lx * lx + ly * ly) || 1;

    return {
      index: peak.index,
      x: p.x,
      y: p.y,
      z: p.z ?? 0,
      dx: lx / len,
      dy: ly / len,
      label: `T${i + 1}`,
    };
  });
}

