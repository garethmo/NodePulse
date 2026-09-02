
node_data = {
    "position": {
        "latitudeI": 515000000,
        "longitudeI": -120000000
    }
}
print(node_data.get("position").get("latitude"))
print(node_data.get("position").get("latitudeI"))
