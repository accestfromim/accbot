import asyncio
import base64
import io
import warnings

from PIL import Image, ImageOps


def compress(raw):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 50_000_000:
                raise ValueError("图片超过 5000 万像素")
            image.seek(0)
            image.draft("RGB", (1024, 1024))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1024, 1024))
            alpha = "A" in image.getbands() or "transparency" in image.info
            output = io.BytesIO()
            image.convert("RGBA" if alpha else "RGB").save(output, "PNG" if alpha else "JPEG", quality=85)
            data = output.getvalue()
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("压缩图片超过 8 MiB")
            return data, "image/png" if alpha else "image/jpeg"


async def load_images(http, assets):
    if not assets or len(assets) > 16:
        raise ValueError("每条消息须包含 1 至 16 张图片")
    images, original_size, encoded_size = [], 0, 0
    for asset in assets:
        if asset.url:
            raw, _, _ = await http.get(asset.url, limit=25 * 1024 * 1024)
        elif asset.data:
            header, encoded = asset.data.split(",", 1)
            if (
                not header.startswith("data:image/")
                or not header.endswith(";base64")
                or len(encoded) > 35_000_000
            ):
                raise ValueError("无效的内联图片")
            raw = base64.b64decode(encoded, validate=True)
        else:
            raise ValueError("图片没有可读取的 URL 或内联数据")
        original_size += len(raw)
        if len(raw) > 25 * 1024 * 1024 or original_size > 50 * 1024 * 1024:
            raise ValueError("图片原始数据超过限制")
        compressed, media_type = await asyncio.to_thread(compress, raw)
        encoded_size += len(compressed)
        if encoded_size > 20 * 1024 * 1024:
            raise ValueError("压缩图片合计超过 20 MiB")
        images.append("data:" + media_type + ";base64," + base64.b64encode(compressed).decode())
    return images
