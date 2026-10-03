import sys, time
import CoreLocation
from Foundation import NSRunLoop, NSDate

def geocode(q, near=None, radius_m=100_000, timeout=10):
    g = CoreLocation.CLGeocoder.alloc().init()
    out = {}
    def done(placemarks, error):
        out["p"] = placemarks; out["e"] = error
    if near:
        region = CoreLocation.CLCircularRegion.alloc().initWithCenter_radius_identifier_(CoreLocation.CLLocationCoordinate2D(near[0], near[1]), radius_m, "near")
        g.geocodeAddressString_inRegion_completionHandler_(q, region, done)
    else:
        g.geocodeAddressString_completionHandler_(q, done)
    t = time.time()
    while "p" not in out and time.time() - t < timeout:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
    res = []
    for p in out.get("p") or []:
        loc = p.location().coordinate()
        res.append((p.name(), loc.latitude, loc.longitude, p.locality(), p.areasOfInterest()))
    return res, out.get("e")

if __name__ == "__main__":
    for q, near in (("Young Family Farm, Little Compton, Rhode Island", (41.5, -71.2)), ("Evelyn's Drive In, Tiverton, Rhode Island", (41.5, -71.2)),
                    ("Ristorante La Martellina", (43.67, 11.29)), ("Santa Croce church, Piazza Matteotti, Greve in Chianti, Tuscany", (43.67, 11.29))):
        print(q, "->", geocode(q, near))
