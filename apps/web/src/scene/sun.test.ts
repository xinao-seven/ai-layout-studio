import { describe, expect, it } from 'vitest'
import type { SceneDocument } from '../contracts/generated/client/rest'
import { eastOfNorth, formatSunTime, solarPosition, sunDate, sunSceneDirection } from './sun'

function utcDate(month: number, day: number, hourUtc: number, minute = 0): Date {
  return new Date(Date.UTC(2026, month - 1, day, hourUtc, minute))
}

describe('solarPosition', () => {
  it('puts the sun near the zenith at the equator on the equinox at solar noon', () => {
    // 2026-03-20 12:00 UTC at longitude 0: solar noon is minutes away from
    // 12:00 UTC (equation of time) and declination is not exactly zero yet,
    // so "near zenith" is the honest assertion.
    const sun = solarPosition(0, 0, utcDate(3, 20, 12))
    expect(sun.elevationDeg).toBeGreaterThan(85)
    expect(sun.elevationDeg).toBeLessThanOrEqual(90)
  })

  it('places noon sun roughly south for northern latitudes', () => {
    // Beijing (39.9N, 116.4E) local noon = 04:00 UTC (UTC+8).
    const sun = solarPosition(39.9, 116.4, utcDate(6, 21, 4))
    expect(sun.elevationDeg).toBeGreaterThan(65)
    expect(sun.azimuthDeg).toBeGreaterThan(150)
    expect(sun.azimuthDeg).toBeLessThan(210)
  })

  it('returns negative elevation at night', () => {
    const sun = solarPosition(39.9, 116.4, utcDate(6, 21, 16))
    expect(sun.elevationDeg).toBeLessThan(0)
  })

  it('puts the morning sun in the east', () => {
    // 08:00 UTC at longitude 0 is mid-morning; azimuth should be < 180 (east side).
    const sun = solarPosition(39.9, 0, utcDate(3, 20, 8))
    expect(sun.elevationDeg).toBeGreaterThan(0)
    expect(sun.azimuthDeg).toBeLessThan(180)
  })
})

describe('sunSceneDirection', () => {
  const scene = {
    north: [0, -1],
  } as unknown as SceneDocument

  it('anchors azimuth 180 (south) along the scene north vector', () => {
    const state = sunSceneDirection(scene, 20, 0, utcDate(3, 20, 12))
    // With north = (0,-1) (scene -Z is north), south is +Z. At lat 20N the
    // equinox noon elevation is ~70°, so the horizontal component is cos(70°).
    expect(state.direction[2]).toBeGreaterThan(0.3)
    expect(Math.abs(state.direction[0])).toBeLessThan(0.05)
    expect(state.elevationDeg).toBeGreaterThan(60)
  })

  it('rotates with the scene north vector', () => {
    const rotated = { north: [1, 0] } as unknown as SceneDocument // +X is north
    const state = sunSceneDirection(rotated, 20, 0, utcDate(3, 20, 12))
    // South = -X now.
    expect(state.direction[0]).toBeLessThan(-0.3)
    expect(Math.abs(state.direction[2])).toBeLessThan(0.05)
  })
})

describe('eastOfNorth / formatSunTime', () => {
  it('derives east perpendicular to north', () => {
    expect(eastOfNorth([0, -1])).toEqual([1, 0])
    expect(eastOfNorth([1, 0])).toEqual([0, 1])
  })

  it('formats the time slider (clamped to 24h)', () => {
    expect(formatSunTime(0)).toBe('00:00')
    expect(formatSunTime(9.5)).toBe('09:30')
    expect(formatSunTime(24)).toBe('00:00')
    expect(formatSunTime(27)).toBe('00:00')
  })
})

describe('sunDate', () => {
  it('converts local solar time via longitude without freezing east mornings', () => {
    // Beijing 116.4E: slider 05:00 vs 06:00 must produce different instants
    // (the old lower clamp froze 0..7.76h to the same UTC midnight).
    const five = sunDate(5, 116.4)
    const six = sunDate(6, 116.4)
    expect(six.getTime() - five.getTime()).toBe(3_600_000)
  })

  it('rolls across midnight for west longitudes / late hours', () => {
    const late = sunDate(23, -120)
    const next = sunDate(24, -120)
    expect(next.getTime() - late.getTime()).toBe(3_600_000)
  })

  it('keeps solar noon at slider 12 for any longitude', () => {
    // True solar time = UTC*60 + 4*lon; for slider 12 the UTC instant minus
    // lon/15h must land at 12h solar regardless of the date.
    const noon = sunDate(12, 116.4)
    const minutesUtc = noon.getUTCHours() * 60 + noon.getUTCMinutes() + noon.getUTCSeconds() / 60
    const solarMinutes = minutesUtc + 4 * 116.4
    expect(solarMinutes / 60).toBeCloseTo(12, 2)
  })
})
