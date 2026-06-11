from PIL import Image

# Open your transparent image 
img = Image.open("raindrop.png").convert("RGBA")

# Save as PNG with alpha channel transparency
img.save("raindrop_transparent.png", format="PNG")

# Save as an ICO file (automatically resizing to standard icon sizes)
img.save("raindrop.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

