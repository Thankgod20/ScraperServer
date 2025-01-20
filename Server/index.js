const express = require("express");
const cors = require("cors");
const fs = require("fs");
const path = require("path");
const app = express();
app.use(cors()); // Enable CORS for all routes
app.use(express.json()); // Parse JSON body

const filePath = "./extractedData.json";

// API to save or update data
app.post("/saveData", (req, res) => {
  const newData = req.body;

  fs.readFile(filePath, "utf8", (err, data) => {
    let existingData = {};
    if (!err) {
      existingData = JSON.parse(data);
    }

    for (const tokenAddress in newData) {
      if (!existingData[tokenAddress]) {
        existingData[tokenAddress] = newData[tokenAddress];
      } else {
        const existingTokenData = existingData[tokenAddress];
        const newTokenData = newData[tokenAddress];

        newTokenData.marketcap.forEach((cap, index) => {
          if (!existingTokenData.marketcap.includes(cap)) {
            existingTokenData.marketcap.push(cap);
            existingTokenData.last3minbuy.push(newTokenData.last3minbuy[index]);
            existingTokenData.last3minbuyer.push(
              newTokenData.last3minbuyer[index]
            );
          }
        });
      }
    }

    fs.writeFile(
      filePath,
      JSON.stringify(existingData, null, 2),
      (writeErr) => {
        if (writeErr) {
          return res.status(500).send("Error saving data");
        }
        res.send("Data saved successfully");
      }
    );
  });
});

const tweetDataToFile = (data) => {
  const tokenFolder = path.join(__dirname, "spltoken");

  // Ensure the 'token' folder exists
  if (!fs.existsSync(tokenFolder)) {
    fs.mkdirSync(tokenFolder);
  }

  // Iterate over each tweet in the request
  for (let tweetUrl in data) {
    const tweetDetails = data[tweetUrl];
    const { address, tweet, likes, retweets, comments, views, time, date } =
      tweetDetails;
    const filePath = path.join(tokenFolder, `${address}.json`);

    // Prepare the structure for saving
    const tweetData = {
      tweet: tweet,
      address: address,
      likes: likes,
      retweets: retweets,
      comments: comments,
      views: views,
      time: time,
      date: date,
    };

    // If the file exists, we need to read it and append the data
    if (fs.existsSync(filePath)) {
      const fileData = JSON.parse(fs.readFileSync(filePath, "utf8"));

      // If the date doesn't exist, create it
      if (!fileData[date]) {
        fileData[date] = {};
      }

      // If the time doesn't exist under the date, create it
      if (!fileData[date][time]) {
        fileData[date][time] = [];
      }

      // Push the new tweet data to the time array
      fileData[date][time].push(tweetData);

      // Save the updated data back to the file
      fs.writeFileSync(filePath, JSON.stringify(fileData, null, 4));
    } else {
      // If file does not exist, create a new structure
      const newData = {
        [date]: {
          [time]: [tweetData],
        },
      };

      // Save new data to the file
      fs.writeFileSync(filePath, JSON.stringify(newData, null, 4));
    }
  }
};
// Route to handle incoming data
app.post("/tweet", (req, res) => {
  const requestData = req.body;
  const tokenFolder = path.join(__dirname, "spltoken");

  // Ensure the 'token' folder exists
  if (!fs.existsSync(tokenFolder)) {
    fs.mkdirSync(tokenFolder);
  }

  for (const [tweetUrl, data] of Object.entries(requestData)) {
    const address = data.address;
    const filePath = path.join(tokenFolder, `${address}.json`);

    let fileData = {};

    // Load existing file if it exists
    if (fs.existsSync(filePath)) {
      const fileContent = fs.readFileSync(filePath, "utf-8");
      fileData = JSON.parse(fileContent);
    }

    // Add new tweet data if the tweet URL doesn't already exist
    if (!fileData[tweetUrl]) {
      let tweet = data.tweet;
      fileData[tweetUrl] = { tweetData: { tweet } }; //data.tweet;
      fs.writeFileSync(filePath, JSON.stringify(fileData, null, 2));
    }
  }

  res.status(200).json({ message: "Data processed successfully." });
});

// POST route to handle incoming data
app.post("/tweeturl", (req, res) => {
  const tweetData = req.body;

  // Validate that the request body is not empty
  if (Object.keys(tweetData).length === 0) {
    return res.status(400).json({ message: "Request body is empty." });
  }

  // Save the data to the file
  saveDataToFile(tweetData);

  // Send a response back to the client
  res.status(200).json({ message: "Data saved successfully" });
});
/*
app.post("/tweet", (req, res) => {
  const data = req.body;

  if (!data || typeof data !== "object") {
    return res.status(400).send("Invalid JSON payload");
  }
  const TOKEN_FOLDER = path.join(__dirname, "tokens");

  // Ensure the token folder exists
  if (!fs.existsSync(TOKEN_FOLDER)) {
    fs.mkdirSync(TOKEN_FOLDER, { recursive: true });
  }
  // Process each entry in the request
  for (const [url, details] of Object.entries(data)) {
    const address = details.address;

    if (!address) {
      console.error(`Address missing for URL: ${url}`);
      continue;
    }

    // Define the file path for the token
    const filePath = path.join(TOKEN_FOLDER, `${address}.json`);

    let fileData = [];
    if (fs.existsSync(filePath)) {
      // Read existing data if the file exists
      const existingData = fs.readFileSync(filePath, "utf8");
      try {
        fileData = JSON.parse(existingData);
      } catch (err) {
        console.error(`Error parsing JSON file: ${filePath}`);
      }
    }

    // Append the new entry to the data array
    fileData.push({ url, ...details });

    // Write the updated data back to the file
    fs.writeFileSync(filePath, JSON.stringify(fileData, null, 2));
  }

  res.send("Data processed successfully.");
});
*/
// Start the server
app.listen(3000, () => {
  console.log("Server running on http://localhost:3000");
});
