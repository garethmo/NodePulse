import meshtastic.protobuf.mesh_pb2 as pb
import google.protobuf.json_format as jf

node_data = {
    "position": {
        "latitudeI": 515000000,
        "longitudeI": -120000000
    }
}
print(node_data.get("position").get("latitude"))
print(node_data.get("position").get("latitudeI"))
