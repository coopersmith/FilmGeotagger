import time
import CoreLocation, MapKit
from Foundation import NSRunLoop, NSDate

def search(q, near, span_deg=1.0, timeout=10):
    req = MapKit.MKLocalSearchRequest.alloc().init()
    req.setNaturalLanguageQuery_(q)
    region = MapKit.MKCoordinateRegionMake(CoreLocation.CLLocationCoordinate2D(near[0], near[1]), MapKit.MKCoordinateSpanMake(span_deg, span_deg))
    req.setRegion_(region)
    s = MapKit.MKLocalSearch.alloc().initWithRequest_(req)
    out = {}
    s.startWithCompletionHandler_(lambda resp, err: out.update(r=resp, e=err))
    t = time.time()
    while "r" not in out and time.time() - t < timeout:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
    res = []
    if out.get("r") is not None:
        for it in out["r"].mapItems():
            c = it.placemark().coordinate()
            res.append((it.name(), round(c.latitude, 5), round(c.longitude, 5), it.placemark().locality()))
    return res, out.get("e")

if __name__ == "__main__":
    for q, near in (("Young Family Farm", (41.5, -71.2)), ("Evelyn's Drive In", (41.5, -71.2)),
                    ("Ristorante La Martellina", (43.67, 11.29)), ("Santa Croce church Greve in Chianti", (43.67, 11.29)), ("New England harbor mooring field with a long causeway bridge", (41.5, -71.2))):
        r, e = search(q, near)
        print(q, "->", r[:3], e)
