# Vulkan MCP Server

This is a Model Context Protocol (MCP) server that provides Vulkan documentation to LLM clients. It serves the Vulkan documentation from the site-index.md file.

## Features

- Provides Vulkan documentation as MCP resources
- Supports searching through the documentation
- Allows retrieving information about specific Vulkan topics

## Installation

1. Clone the repository
2. Navigate to the vulkan directory
3. Install dependencies:

```bash
npm install
```

4. Build the project:

```bash
npm run build
```

## Usage

Start the server:

```bash
npm start
```

The server runs on stdio, so it can be used with any MCP client that supports stdio transport.

## Available Resources

- `vulkan-site-index`: The main Vulkan documentation site index

## Available Tools

- `search-vulkan-docs`: Search Vulkan documentation for specific topics
  - Parameters:
    - `query`: The search query for Vulkan documentation

- `get-vulkan-topic`: Get information about a specific Vulkan topic
  - Parameters:
    - `topic`: The Vulkan topic to retrieve information about

## Example Usage with an MCP Client

```javascript
// Example of using the Vulkan MCP server with a client
import { McpClient } from "@modelcontextprotocol/sdk/client/mcp.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

async function main() {
  // Connect to the Vulkan MCP server
  const transport = new StdioClientTransport({
    command: "node build/index.js",
    cwd: "/path/to/vulkan-mcp-server",
  });

  const client = new McpClient();
  await client.connect(transport);

  // List available resources
  const resources = await client.resources.list();
  console.log("Available resources:", resources);

  // Get the Vulkan site index
  const siteIndex = await client.resources.lookup("vulkan-site-index");
  console.log("Vulkan site index:", siteIndex);

  // Search for a specific topic
  const searchResult = await client.tools.call("search-vulkan-docs", {
    query: "depth buffer",
  });
  console.log("Search result:", searchResult);

  // Get information about a specific topic
  const topicInfo = await client.tools.call("get-vulkan-topic", {
    topic: "Atomics",
  });
  console.log("Topic info:", topicInfo);
}

main().catch(console.error);
```

## License

This project is licensed under the MIT License.
