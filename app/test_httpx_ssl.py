import ssl
import httpx

print("SSL:", ssl.OPENSSL_VERSION)

ctx = ssl.create_default_context()

print("before")

client = httpx.Client(verify=ctx)

print("after")