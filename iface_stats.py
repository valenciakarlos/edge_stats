import json
import pandas as pd

# Path to the JSON file
file_path = "lrouter_port_stats.json"

# Load JSON data
with open(file_path, "r") as f:
    data = json.load(f)

# Extract ifuuid, name, and everything under stats
extracted_data = []
for entry in data:
    base_info = {
        "ifuuid": entry.get("ifuuid"),
        "name": entry.get("name")
    }
    stats = entry.get("stats", {})
    
    # Combine base info with stats
    full_entry = {**base_info, **stats}
    extracted_data.append(full_entry)

# Convert to a DataFrame
df = pd.DataFrame(extracted_data)

# Display the DataFrame
print(df)

df.to_csv("full_stats_output.csv", index=False)
