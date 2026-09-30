/** Read a latitude and longitude out of whatever the user pasted.
 *  Accepts "43.7696, 11.2558", "43.7696 11.2558", "43.7696° N, 11.2558° E", degrees-minutes-seconds
 *  ("43°46'10.6\"N 11°15'20.9\"E"), and map links carrying "@lat,lon", "ll=lat,lon" or "q=lat,lon". */
export function parseCoords(text: string): { lat: number; lon: number } | null {
  const s = text.trim();
  if (!s) return null;
  const ok = (lat: number, lon: number) => (Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null);

  const link = s.match(/(?:@|[?&](?:ll|q|sll|center|query)=)(-?\d+(?:\.\d+)?)(?:,|%2C)\s*(-?\d+(?:\.\d+)?)/i);
  if (link) return ok(Number(link[1]), Number(link[2]));

  const dms = [...s.matchAll(/(\d+(?:\.\d+)?)\s*°\s*(?:(\d+(?:\.\d+)?)\s*['′]\s*)?(?:(\d+(?:\.\d+)?)\s*(?:"|″|'')\s*)?([NSEW])/gi)];
  if (dms.length === 2) {
    const val = (m: RegExpMatchArray) => {
      const v = Number(m[1]) + Number(m[2] ?? 0) / 60 + Number(m[3] ?? 0) / 3600;
      return /[SW]/i.test(m[4]) ? -v : v;
    };
    const a = dms.find((m) => /[NS]/i.test(m[4]));
    const b = dms.find((m) => /[EW]/i.test(m[4]));
    if (a && b) return ok(val(a), val(b));
  }

  const nums = s.replace(/°/g, " ").match(/-?\d+(?:\.\d+)?/g);
  if (nums && nums.length === 2) return ok(Number(nums[0]), Number(nums[1]));
  return null;
}
