import type { SceneDocument } from '../contracts/generated/client/rest'
import type { Vec2 } from './geometry'

/**
 * Daylight layer (PRD FR-22): solar position from the NOAA simplified
 * equations, anchored to the scene north vector so the compass, solver's
 * "bed facing south" semantics and the shadows agree on directions.
 */

export interface SunState {
  /** Degrees clockwise from north (0 = north, 90 = east, 180 = south). */
  azimuthDeg: number
  /** Degrees above the horizon; negative means the sun is below it. */
  elevationDeg: number
}

const RAD = Math.PI / 180

function dayOfYear(date: Date): number {
  const start = Date.UTC(date.getUTCFullYear(), 0, 1)
  const current = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())
  return Math.round((current - start) / 86_400_000) + 1
}

/**
 * Solar position for a UTC timestamp. `fractionalHourUtc` is 0–24 UTC; the
 * caller derives it from the time slider plus a fixed timezone offset.
 */
export function solarPosition(latitudeDeg: number, longitudeDeg: number, date: Date): SunState {
  const latitude = latitudeDeg * RAD
  const fractionalYear =
    ((2 * Math.PI) / 365) * (dayOfYear(date) - 1 + (date.getUTCHours() - 12) / 24)
  const equationTimeMinutes =
    229.18 *
    (0.000075 +
      0.001868 * Math.cos(fractionalYear) -
      0.032077 * Math.sin(fractionalYear) -
      0.014615 * Math.cos(2 * fractionalYear) -
      0.040849 * Math.sin(2 * fractionalYear))
  const declination =
    0.006918 -
    0.399912 * Math.cos(fractionalYear) +
    0.070257 * Math.sin(fractionalYear) -
    0.006758 * Math.cos(2 * fractionalYear) +
    0.000907 * Math.sin(2 * fractionalYear) -
    0.002697 * Math.cos(3 * fractionalYear) +
    0.00148 * Math.sin(3 * fractionalYear)

  const minutesUtc = date.getUTCHours() * 60 + date.getUTCMinutes()
  const trueSolarTime = minutesUtc + equationTimeMinutes + 4 * longitudeDeg
  const hourAngle = trueSolarTime / 4 - 180 // degrees, negative before solar noon

  const cosZenith =
    Math.sin(latitude) * Math.sin(declination) +
    Math.cos(latitude) * Math.cos(declination) * Math.cos(hourAngle * RAD)
  const zenith = Math.acos(Math.max(-1, Math.min(1, cosZenith)))
  const elevationDeg = 90 - zenith / RAD

  const azimuthCos =
    (Math.sin(latitude) * cosZenith - Math.sin(declination)) /
    Math.max(1e-9, Math.cos(latitude) * Math.sin(zenith))
  const azimuthClamped = Math.acos(Math.max(-1, Math.min(1, azimuthCos))) / RAD
  const azimuthDeg = hourAngle > 0 ? (azimuthClamped + 180) % 360 : (540 - azimuthClamped) % 360

  return { azimuthDeg, elevationDeg }
}

/** Scene-space east direction for a normalized north vector. */
export function eastOfNorth(north: Vec2): Vec2 {
  const length = Math.hypot(north[0], north[1]) || 1
  const [nx, nz] = [north[0] / length, north[1] / length]
  // cross(north, up) in three.js (Y-up, right-handed) gives east. `+ 0`
  // normalises -0 so structural equality on coordinates is stable.
  return [-nz + 0, nx + 0]
}

export interface SunSceneState {
  /** Direction from the scene toward the sun, normalized. */
  direction: [number, number, number]
  elevationDeg: number
}

/**
 * Sun direction expressed in scene coordinates: azimuth rotates from the
 * scene north vector clockwise, elevation lifts toward +Y.
 */
export function sunSceneDirection(
  scene: SceneDocument,
  latitudeDeg: number,
  longitudeDeg: number,
  date: Date,
): SunSceneState {
  const { azimuthDeg, elevationDeg } = solarPosition(latitudeDeg, longitudeDeg, date)
  const north = scene.north as Vec2
  const east = eastOfNorth(north)
  const azimuth = azimuthDeg * RAD
  const elevation = elevationDeg * RAD
  const horizontal = Math.max(0, Math.cos(elevation))
  return {
    direction: [
      north[0] * Math.cos(azimuth) * horizontal + east[0] * Math.sin(azimuth) * horizontal,
      Math.sin(elevation),
      north[1] * Math.cos(azimuth) * horizontal + east[1] * Math.sin(azimuth) * horizontal,
    ],
    elevationDeg,
  }
}

export function formatSunTime(hours: number): string {
  const clamped = Math.max(0, Math.min(24, hours))
  const hh = Math.floor(clamped) % 24
  const mm = Math.floor((clamped - Math.floor(clamped)) * 60)
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}

/**
 * Slider hours are interpreted as *local mean solar time* at the scene
 * longitude, so 12:00 on the timeline is solar noon regardless of timezone:
 * the underlying UTC instant is shifted by -longitude/15. The shift may be
 * negative (east longitudes, early morning) or exceed 24 h (west longitudes,
 * late evening); Date.UTC rolls it into the neighbouring day, which keeps the
 * solar geometry continuous across the whole slider range.
 */
export function sunDate(hoursLocalSolar: number, longitudeDeg: number): Date {
  const now = new Date()
  const utcHours = hoursLocalSolar - longitudeDeg / 15
  const utcMs =
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) + utcHours * 3_600_000
  return new Date(utcMs)
}
