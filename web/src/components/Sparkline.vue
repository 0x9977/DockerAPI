<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    points?: number[] | null;
    width?: number;
    height?: number;
    color?: string;
    fill?: boolean;
  }>(),
  { points: () => [], width: 120, height: 32, color: '#8f9bff', fill: true }
);

const PAD = 2;

const coords = computed<Array<[number, number]>>(() => {
  const pts = (props.points ?? []).filter((v) => typeof v === 'number' && Number.isFinite(v));
  if (pts.length === 0) return [];
  const w = Math.max(props.width - PAD * 2, 1);
  const h = Math.max(props.height - PAD * 2, 1);
  let max = Math.max(...pts);
  let min = Math.min(...pts);
  if (!Number.isFinite(max)) max = 1;
  if (!Number.isFinite(min)) min = 0;
  if (max === min) max = min + 1;
  const step = pts.length > 1 ? w / (pts.length - 1) : 0;
  return pts.map((v, i) => {
    const x = PAD + (pts.length > 1 ? i * step : w / 2);
    const y = PAD + h - ((v - min) / (max - min)) * h;
    return [x, y] as [number, number];
  });
});

const linePath = computed(() =>
  coords.value.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ')
);

const areaPath = computed(() => {
  if (!props.fill || coords.value.length === 0) return '';
  const bottom = props.height - PAD;
  const left = PAD;
  const right = props.width - PAD;
  return `${linePath.value} L ${right.toFixed(2)} ${bottom} L ${left} ${bottom} Z`;
});
</script>

<template>
  <svg
    v-if="linePath"
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    preserveAspectRatio="none"
    class="sparkline"
    role="img"
  >
    <path v-if="areaPath" :d="areaPath" :fill="color" opacity="0.14" />
    <path
      :d="linePath"
      fill="none"
      :stroke="color"
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
      vector-effect="non-scaling-stroke"
    />
  </svg>
  <span v-else class="sparkline-empty dim">—</span>
</template>

<style scoped>
.sparkline {
  display: block;
}

.sparkline-empty {
  font-size: 12px;
}
</style>
