#!/bin/bash
set -euo pipefail

# Required environment variables
: "${PLUGIN_CONFLUENCE_URL:?Need to set PLUGIN_CONFLUENCE_URL}"
: "${PLUGIN_CONFLUENCE_TOKEN:?Need to set PLUGIN_CONFLUENCE_TOKEN}"
: "${PLUGIN_CONFLUENCE_SPACE:?Need to set PLUGIN_CONFLUENCE_SPACE}"
: "${PLUGIN_CONFLUENCE_ROOT_FOLDER:?Need to set PLUGIN_CONFLUENCE_ROOT_FOLDER}"
: "${PLUGIN_DOCS_ROOT_FOLDER:?Need to set PLUGIN_DOCS_ROOT_FOLDER}"

# Find or create a page in Confluence, return its ID
# Args: $1=title, $2=parentId, $3=content(optional)
find_or_create_page() {
    local title="$1"
    local parentId="$2"
    local content="${3:-}"

    # Search page
    local searchResp
    searchResp=$(curl -sS -H "Authorization: Bearer ${PLUGIN_CONFLUENCE_TOKEN}" \
        -G "$PLUGIN_CONFLUENCE_URL/rest/api/content" \
        --data-urlencode "title=$title" \
        --data-urlencode "spaceKey=$PLUGIN_CONFLUENCE_SPACE" \
        --data-urlencode "expand=version" \
        --data-urlencode "limit=1")

    local id
    id=$(echo "$searchResp" | jq -r '.results[0].id // empty')

    if [[ -n "$id" ]]; then
        # Update
        local version
        version=$(echo "$searchResp" | jq '.results[0].version.number + 1')

        curl -sS -H "Authorization: Bearer ${PLUGIN_CONFLUENCE_TOKEN}" \
            -X PUT \
            -H "Content-Type: application/json" \
            -d "$(jq -nc \
                --arg title "$title" \
                --arg parentId "$parentId" \
                --arg content "$content" \
                --argjson version "$version" \
                --arg id "$id" \
                '{id:$id, type:"page", title:$title, space:{key:env.PLUGIN_CONFLUENCE_SPACE}, ancestors:[{id:$parentId}], body:{storage:{value:$content, representation:"storage"}}, version:{number:$version}}')" \
            "$PLUGIN_CONFLUENCE_URL/rest/api/content/$id" >/dev/null
    else
        # Create
        id=$(curl -sS -H "Authorization: Bearer ${PLUGIN_CONFLUENCE_TOKEN}" \
            -X POST \
            -H "Content-Type: application/json" \
            -d "$(jq -nc \
                --arg title "$title" \
                --arg parentId "$parentId" \
                --arg content "$content" \
                '{type:"page", title:$title, space:{key:env.PLUGIN_CONFLUENCE_SPACE}, ancestors:[{id:$parentId}], body:{storage:{value:$content, representation:"storage"}}}')" \
            "$PLUGIN_CONFLUENCE_URL/rest/api/content")
    fi

    echo "$id"
}

# Recursively upload directory
process_dir() {
    local dir="$1"
    local parentId="$2"

    echo "Processing directory: $dir (Parent ID: $parentId)" >&2

    for entry in "$dir"/*; do
        [[ -e "$entry" ]] || continue
        local name=$(basename "$entry")

        echo "  Found entry: $entry (Name: $name)" >&2

        if [[ -d "$entry" ]]; then
            echo "  Entry is a directory, recursing..." >&2
            local currentPageId
            currentPageId=$(find_or_create_page "$(basename "$entry")" "$parentId" "")
            echo "  Created/Found page ID: $currentPageId for directory $(basename "$dir")" >&2
            process_dir "$entry" "$currentPageId"
        elif [[ "$entry" =~ \.md$|\.MD$ ]]; then
            echo "  Entry is a markdown file, converting to HTML..." >&2
            local html
            html=$(pandoc --from markdown --to html "$entry")
            echo "  Converted markdown to HTML for file: $entry" >&2
            name=${name%.md} #remove .md if exist
            name=${name%.MD} #remove .MD if exist
            echo "  Creating/Updating page: $name (Parent ID: $parentId)" >&2
            local new_file
            new_file=$(find_or_create_page "$name" "$parentId" "$html")
            echo $new_file
        else
            echo "  Skipping entry: $entry (not a directory or markdown file)" >&2
        fi
    done
}

ROOT_ID=$(find_or_create_page "$PLUGIN_CONFLUENCE_ROOT_FOLDER" "" "")
process_dir "$PLUGIN_DOCS_ROOT_FOLDER" "$ROOT_ID"

echo "Sync completed"













wfw
ssmmm

dzfdsdfsddsfdfsdfsdfsdsfddf

dfsadfa
sadsafdsfd




dljaasmknalsd