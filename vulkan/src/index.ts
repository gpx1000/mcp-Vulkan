import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

// Get the directory name of the current module
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Path to the Vulkan documentation file
const SITE_INDEX_PATH = path.resolve(__dirname, "../../site-index.md");

// Read the Vulkan documentation file
const siteIndexContent = fs.readFileSync(SITE_INDEX_PATH, "utf-8");

// Create server instance
const server = new McpServer({
  name: "vulkan-docs",
  version: "1.0.0",
  capabilities: {
    resources: {
      list: true,
      lookup: true,
    },
    tools: {},
  },
});

// Register Vulkan documentation resources
server.resource(
  "vulkan-site-index",
  "vulkan-site-index",
  () => {
    return {
      contents: [
        {
          uri: "vulkan-site-index",
          text: siteIndexContent,
          mimeType: "text/markdown"
        }
      ]
    };
  }
);

// Register a tool to search Vulkan documentation
server.tool(
  "search-vulkan-docs",
  "Search Vulkan documentation for specific topics",
  {
    query: z.string().describe("The search query for Vulkan documentation"),
  },
  async ({ query }) => {
    // Simple search implementation - in a real application, this could be more sophisticated
    const searchResults = [];

    // Search in site index
    if (siteIndexContent.toLowerCase().includes(query.toLowerCase())) {
      const lines = siteIndexContent.split('\n');
      const matchingLines = lines.filter(line =>
        line.toLowerCase().includes(query.toLowerCase())
      );

      if (matchingLines.length > 0) {
        searchResults.push(`Found in site-index.md:\n${matchingLines.slice(0, 10).join('\n')}`);

        if (matchingLines.length > 10) {
          searchResults.push(`... and ${matchingLines.length - 10} more matches`);
        }
      }
    }

    if (searchResults.length === 0) {
      return {
        content: [
          {
            type: "text",
            text: `No results found for query: "${query}"`,
          },
        ],
      };
    }

    return {
      content: [
        {
          type: "text",
          text: searchResults.join('\n\n'),
        },
      ],
    };
  },
);

// Register a tool to get specific Vulkan topics
server.tool(
  "get-vulkan-topic",
  "Get information about a specific Vulkan topic",
  {
    topic: z.string().describe("The Vulkan topic to retrieve information about"),
  },
  async ({ topic }) => {
    // Simple topic extraction - in a real application, this could be more sophisticated
    const lines = siteIndexContent.split('\n');
    const topicLines = [];
    let foundTopic = false;
    let sectionDepth = 0;

    for (const line of lines) {
      // Check if this line contains the topic
      if (!foundTopic && line.toLowerCase().includes(topic.toLowerCase())) {
        foundTopic = true;

        // Determine the section depth by counting the number of '#' characters
        sectionDepth = (line.match(/^#+/) || [''])[0].length;
        topicLines.push(line);
        continue;
      }

      // If we found the topic, keep adding lines until we reach a heading of the same or lower depth
      if (foundTopic) {
        // Check if this line is a heading of the same or lower depth
        const headingMatch = line.match(/^(#+)/);
        if (headingMatch && headingMatch[1].length <= sectionDepth) {
          break;
        }

        topicLines.push(line);
      }
    }

    if (topicLines.length === 0) {
      return {
        content: [
          {
            type: "text",
            text: `No information found for topic: "${topic}"`,
          },
        ],
      };
    }

    return {
      content: [
        {
          type: "text",
          text: topicLines.join('\n'),
        },
      ],
    };
  },
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Vulkan MCP Server running on stdio");
}

main().catch((error) => {
  console.error("Fatal error in main():", error);
  process.exit(1);
});
