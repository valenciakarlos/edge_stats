import json
import pandas as pd

# Path to the JSON file
file_path = "lrouter_port_stats.json"

# Load JSON data from file
with open(file_path, "r") as f:
    data = json.load(f)

# Access the list under 'lrouter_port_stats'
port_stats = data.get("lrouter_port_stats", [])

# Extract ifuuid, name, and everything under stats
extracted_data = []
for entry in port_stats:
    base_info = {
        "ifuuid": entry.get("ifuuid"),
        "name": entry.get("name")
    }
    stats = entry.get("stats", {})
    
    # Merge base info with stats
    full_entry = {**base_info, **stats}
    extracted_data.append(full_entry)

# Convert to a pandas DataFrame
df = pd.DataFrame(extracted_data)

# Display the DataFrame
print(df)

df.to_csv("parsed_lrouter_stats.csv", index=False)
