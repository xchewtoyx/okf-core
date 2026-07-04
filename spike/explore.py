import io
from ruamel.yaml import YAML

yaml = YAML(typ="rt")
yaml.preserve_quotes = True

def rt(src, mutate=None):
    data = yaml.load(src)
    if mutate:
        mutate(data)
    buf = io.StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()

print("=== comments + quoting + order ===")
src = 'type: concept   # a comment\ntitle: "Quoted Title"\ntags: [a, b]\n'
out = rt(src, lambda d: d.__setitem__("type", "updated"))
print(repr(out))

print("=== unknown/untargeted richer value untouched ===")
src = "type: concept\nnested:\n  a: 1\n  b: [1,2,3]\n"
out = rt(src, lambda d: d.__setitem__("type", "updated"))
print(repr(out))

print("=== duplicate keys ===")
src = "type: concept\ntitle: First\ntitle: Second\n"
try:
    rt(src)
    print("NO ERROR (silently overwrote)")
except Exception as e:
    print("RAISED:", type(e).__name__, e)

print("=== anchors/aliases: edit anchor key ===")
src = "type: concept\na: &shared value\nb: *shared\n"
data = yaml.load(src)
print("a is b (same object)?", data["a"] is data["b"])
data["a"] = "updated"
buf = io.StringIO(); yaml.dump(data, buf)
print(repr(buf.getvalue()))

print("=== dates ===")
src = "type: concept\nwhen: 2026-07-04\n"
data = yaml.load(src)
print(type(data["when"]), data["when"])

print("=== zero width null ===")
src = "owner:\nother: 1\n"
out = rt(src, lambda d: d.__setitem__("owner", "team"))
print(repr(out))

print("=== mixed line endings ===")
src = "type: concept\r\ntitle: x\r\n"
out = rt(src, lambda d: d.__setitem__("type", "updated"))
print(repr(out))
