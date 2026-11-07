"""Bounded public HTTP reads. DNS is checked and the connection uses that same IP."""

import asyncio
import ipaddress
import socket

import httpx


class PublicHTTP:
    async def get(self, raw_url, *, limit=2 * 1024 * 1024):
        url = httpx.URL(raw_url)
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            for _ in range(6):
                if url.scheme not in {"http", "https"} or not url.host or url.username or url.password:
                    raise ValueError("只允许不含凭据的公开 HTTP/HTTPS URL")
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    url.host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM
                )
                ips = [ipaddress.ip_address(a[4][0]) for a in addresses]
                if not ips or any(
                    not ip.is_global
                    or (
                        isinstance(ip, ipaddress.IPv6Address)
                        and ip.ipv4_mapped
                        and not ip.ipv4_mapped.is_global
                    )
                    for ip in ips
                ):
                    raise ValueError("不能访问非公开网络地址")
                # Host and TLS SNI retain the original hostname, without a second DNS lookup.
                target = url.copy_with(host=str(ips[0]))
                headers = {"Host": url.netloc.decode(), "User-Agent": "accbot/0.1", "Accept": "*/*"}
                async with client.stream(
                    "GET", target, headers=headers, extensions={"sni_hostname": url.host}
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        url = url.join(response.headers["location"])
                        continue
                    response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise ValueError("资源超过大小限制")
                        chunks.append(chunk)
                    return b"".join(chunks), response.headers.get("content-type", ""), str(url)
        raise ValueError("重定向次数过多")
