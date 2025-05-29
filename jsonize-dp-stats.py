def jsonize_entries(file_content) -> list:
    """
    Jsonize dp-stats.log entries

    dp-stats.log entries look like a list of json dictionaries except there is
    a catch, it is not a valid json list as a whole. Thus we convert the
    following,
    {
    ...
    }
    {
    ...
    }
    to
    [
        {
        ...
        },
        {
        ...
        }
    ]
    """
    import json
    json_list: list = []

    # Split the file content into individual JSON objects
    # The first and last entries might be incomplete, so handle them separately
    # The rest of the entries are complete JSON objects already
    json_entries = file_content.strip().split('\n}\n{')
    json_entries[0] = json_entries[0] + '}'
    json_entries[-1] = '{' + json_entries[-1]

    # Add the curly braces back to the entries post split
    for i in range(1, len(json_entries) - 1):
        json_entries[i] = '{' + json_entries[i] + '}'

    # Load the JSON objects into a list
    for entry in json_entries:
        json_dict = json.loads(entry)
        json_list.append(json_dict)

    return json_list


def main():
    # Load dp-stats.log or similar file
    with open('dp-stats.log', 'r') as f:
        file_content = f.read()
    
    entries = jsonize_entries(file_content)

    # For demonstration, print first parsed entry
    print("Entries of type:")
    print(type(entries))
    print(entries[1])



if __name__ == "__main__":
    main()

