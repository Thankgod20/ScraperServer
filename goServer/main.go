package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/blocto/solana-go-sdk/common"
	"github.com/blocto/solana-go-sdk/program/metaplex/token_metadata"
	"github.com/gagliardetto/solana-go"
	"github.com/gagliardetto/solana-go/rpc"
	"github.com/go-redis/redis"
	"github.com/gorilla/websocket"
	holders "github.com/thankgod20/scraperServer/Holders"
)

const (
	filePath_      = "extractedData.json"
	staticFolder   = "/static"
	tokenFolder    = "/spltoken"
	contentTypeKey = "Content-Type"
	contentTypeVal = "application/json"
)

// ----------------------
// Structures for Add-Address and Migration Events
// ----------------------

type NFTMetadata struct {
	Name   string `json:"name"`
	Symbol string `json:"symbol"`
	URI    string `json:"uri"`
}
type NullTime struct {
	time.Time
}
type Transfer struct {
	Address string   `json:"address"`
	Amount  float64  `json:"amount"`
	Time    NullTime `json:"time"`
	Price   float64  `json:"price"`
}

type Address struct {
	Address      string     `json:"address"`
	Name         string     `json:"name"`
	Symbol       string     `json:"symbol"`
	Index        int64      `json:"index"`
	AddedAt      *time.Time `json:"added_at"`
	LastActivity *time.Time `json:"last_activity"`
}
type Cookies struct {
	AuthToken string `json:"auth_token"`
	Ct0       string `json:"ct0"`
}
type Proxy struct {
	Address string `json:"proxies"`
}
type TokenMetadata struct {
	Name   string `json:"name"`
	Symbol string `json:"symbol"`
	URI    string `json:"uri"`
}

// MigrationEvent represents the JSON payload for a migration event.
type MigrationEvent struct {
	Signature string `json:"signature"`
	Mint      string `json:"mint"`
	TxType    string `json:"txType"`
	Pool      string `json:"pool"`
}

var RedisClient *redis.Client

func init() {
	RedisClient = redis.NewClient(&redis.Options{
		Addr:     "localhost:6379", // Update with your Redis server address
		Password: "",               // Update with your Redis password, if any
		DB:       0,                // Default DB
	})

	// Test the connection
	_, err := RedisClient.Ping().Result()
	if err != nil {
		log.Fatalf("Failed to connect to Redis: %v", err)
	}
	fmt.Println("Connected to Redis!")
}

// API response structure
type APIResponse struct {
	Keys   []string      `json:"keys"`
	Values []interface{} `json:"values"`
}

// ----------------------
// HTTP Handlers (Unchanged)
// ----------------------

func fetchHolderKeysAndDataHandler(w http.ResponseWriter, r *http.Request) {
	searchWord := r.URL.Query().Get("search")
	pattern := fmt.Sprintf("%s", searchWord)
	fmt.Println("Partern", pattern)

	client := holders.NewBitqueryClient()

	output, err := client.UpdateAndGetTransfers(pattern)
	if err != nil {
		log.Fatalf("Error updating and getting transfers: %v", err)
	}
	var transfers []Transfer
	if err := json.Unmarshal([]byte(output), &transfers); err != nil {
		log.Fatalf("Error decoding transfers: %v", err)
	}
	if len(transfers) == 0 {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"message": "No matching keys found"})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(transfers); err != nil {
		http.Error(w, fmt.Sprintf("Failed to encode response: %v", err), http.StatusInternalServerError)
	}
}

func fetchKeysAndDataHandler(w http.ResponseWriter, r *http.Request) {
	searchWord := r.URL.Query().Get("search")
	pattern := fmt.Sprintf("*spltoken:%s*", searchWord)

	keys, err := RedisClient.Keys(pattern).Result()
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to retrieve keys: %v", err), http.StatusInternalServerError)
		return
	}

	var values []interface{}
	for _, key := range keys {
		value, err := RedisClient.Get(key).Result()
		if err != nil {
			log.Printf("Error retrieving value for key %s: %v", key, err)
			continue
		}
		var rawValue interface{}
		if err := json.Unmarshal([]byte(value), &rawValue); err == nil {
			values = append(values, rawValue)
		} else {
			cleanedValue := strings.ReplaceAll(value, "\\\"", "\"")
			values = append(values, cleanedValue)
		}
	}

	if len(values) == 0 {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"message": "No matching keys found"})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(values); err != nil {
		http.Error(w, fmt.Sprintf("Failed to encode response: %v", err), http.StatusInternalServerError)
	}
}

func getTokenMetadata(w http.ResponseWriter, r *http.Request) {
	mintAddress := r.URL.Query().Get("mint")
	if mintAddress == "" {
		http.Error(w, "Missing 'mint' query parameter", http.StatusBadRequest)
		return
	}
	mintPubKey, err := solana.PublicKeyFromBase58(mintAddress)
	if err != nil {
		http.Error(w, fmt.Sprintf("Invalid mint address: %v", err), http.StatusBadRequest)
		return
	}
	client := rpc.New(rpc.MainNetBeta_RPC)
	commonMintPubKey := common.PublicKeyFromBytes(mintPubKey.Bytes())
	metadataPubKey, err := token_metadata.GetTokenMetaPubkey(commonMintPubKey)
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to derive metadata public key: %v", err), http.StatusInternalServerError)
		return
	}
	accountInfo, err := client.GetAccountInfo(context.Background(), solana.PublicKeyFromBytes(metadataPubKey[:]))
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to fetch account info: %v", err), http.StatusInternalServerError)
		return
	}
	if accountInfo == nil || accountInfo.Value == nil {
		http.Error(w, "Metadata account not found", http.StatusNotFound)
		return
	}
	data := accountInfo.Value.Data.GetBinary()
	if data == nil {
		http.Error(w, "Failed to retrieve binary data from account info", http.StatusInternalServerError)
		return
	}
	metadata, err := token_metadata.MetadataDeserialize(data)
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to deserialize metadata: %v", err), http.StatusInternalServerError)
		return
	}

	response := NFTMetadata{
		Name:   metadata.Data.Name,
		Symbol: metadata.Data.Symbol,
		URI:    metadata.Data.Uri,
	}
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(response); err != nil {
		http.Error(w, fmt.Sprintf("Failed to encode response: %v", err), http.StatusInternalServerError)
	}
}

func saveDataHandler(w http.ResponseWriter, r *http.Request) {
	setHeaders(w)
	if r.Method != http.MethodPost {
		http.Error(w, "Invalid request method", http.StatusMethodNotAllowed)
		return
	}

	var newData map[string]map[string]interface{}
	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "Unable to read request body", http.StatusInternalServerError)
		return
	}
	defer r.Body.Close()
	fmt.Println("Raw body:", string(body))
	if err := json.Unmarshal(body, &newData); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}
	rootDir := ensureFolder(staticFolder)
	existingData := make(map[string]map[string]interface{})
	filePath := filepath.Join(rootDir, filePath_)
	if _, err := os.Stat(filePath); err == nil {
		data, err := ioutil.ReadFile(filePath)
		if err == nil {
			json.Unmarshal(data, &existingData)
		}
	}
	for tokenAddress, tokenData := range newData {
		if _, exists := existingData[tokenAddress]; !exists {
			existingData[tokenAddress] = tokenData
		}
	}
	data, _ := json.MarshalIndent(existingData, "", "  ")
	if err := ioutil.WriteFile(filePath, data, 0644); err != nil {
		http.Error(w, "Error saving data", http.StatusInternalServerError)
		return
	}

	w.Write([]byte("Data saved successfully"))
}

func tweetHandler(w http.ResponseWriter, r *http.Request) {
	setHeaders(w)
	if r.Method != http.MethodPost {
		http.Error(w, "Invalid request method", http.StatusMethodNotAllowed)
		return
	}

	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "Unable to read request body", http.StatusInternalServerError)
		return
	}
	defer r.Body.Close()
	fmt.Println("Recieveing", string(body))
	var requestData map[string]map[string]interface{}
	if err := json.Unmarshal(body, &requestData); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}

	rootDir := ensureFolder(tokenFolder)
	for tweetURL, data := range requestData {
		address := fmt.Sprintf("%v", data["address"])
		if address == "" {
			continue
		}
		filePath := filepath.Join(rootDir, address+".json")
		var fileData map[string]interface{}
		if _, err := os.Stat(filePath); err == nil {
			fileContent, _ := ioutil.ReadFile(filePath)
			json.Unmarshal(fileContent, &fileData)
		} else {
			fileData = make(map[string]interface{})
		}
		tweetContent, ok := data["tweet"].(string)
		if !ok || tweetContent == "" {
			fmt.Println("Missing or invalid tweet content")
			continue
		}
		if _, exists := fileData[tweetURL]; !exists {
			fileData[tweetURL] = map[string]interface{}{
				"tweetData": map[string]string{
					"tweet": tweetContent,
				},
			}
			dataToWrite, _ := json.MarshalIndent(fileData, "", "  ")
			ioutil.WriteFile(filePath, dataToWrite, 0644)
		}
	}

	w.Write([]byte(`{"message": "Data processed successfully."}`))
}

func tweetURLHandler(w http.ResponseWriter, r *http.Request) {
	setHeaders(w)
	if r.Method != http.MethodPost {
		http.Error(w, "Invalid request method", http.StatusMethodNotAllowed)
		return
	}

	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "Unable to read request body", http.StatusInternalServerError)
		return
	}
	defer r.Body.Close()

	if strings.TrimSpace(string(body)) == "" {
		http.Error(w, "Request body is empty", http.StatusBadRequest)
		return
	}

	w.Write([]byte(`{"message": "Data saved successfully"}`))
}

func ensureFolder(folder string) string {
	rootDir := filepath.Join("..", folder)
	if err := os.MkdirAll(rootDir, os.ModePerm); err != nil {
		fmt.Println(err)
	}
	return rootDir
}

func contains(slice []string, item string) bool {
	for _, value := range slice {
		if value == item {
			return true
		}
	}
	return false
}

// ----------------------
// New Helper Function to Get Token Metadata via API
// ----------------------

func getTokenMetadataFromAPI(mint string) (*TokenMetadata, error) {
	url := fmt.Sprintf("http://localhost:3300/api/token-metadata?mint=%s", mint)
	resp, err := http.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("failed to fetch token metadata, status code: %d", resp.StatusCode)
	}
	var metadata TokenMetadata
	if err := json.NewDecoder(resp.Body).Decode(&metadata); err != nil {
		return nil, err
	}
	return &metadata, nil
}

// ----------------------
// WebSocket Migration Subscription and Address Update Logic
// ----------------------

// subscribeMigration connects to the WebSocket endpoint and subscribes for migration events.
func subscribeMigration() {
	wsURL := "wss://pumpportal.fun/api/data"
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		log.Println("WebSocket dial error:", err)
		return
	}
	defer conn.Close()
	log.Println("Connected to migration subscription at", wsURL)

	// Send subscription request for migration events.
	subscription := map[string]string{"method": "subscribeMigration"}
	msg, err := json.Marshal(subscription)
	if err != nil {
		log.Println("Error marshalling subscription:", err)
		return
	}

	if err = conn.WriteMessage(websocket.TextMessage, msg); err != nil {
		log.Println("Error sending subscription message:", err)
		return
	}
	log.Println("Sent migration subscription message.")

	// Continuously read messages.
	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			log.Println("Error reading message:", err)
			break
		}
		log.Println("Received migration event:", string(message))
		// Parse the incoming migration event.
		var event MigrationEvent
		err = json.Unmarshal(message, &event)
		if err != nil {
			log.Println("Error parsing migration event:", err)
			continue
		}
		// Only process if it is a migration event.
		if event.TxType == "migrate" {
			err = addMigratedTokenToAddresses(event)
			if err != nil {
				log.Println("Error adding migrated token to addresses:", err)
			} else {
				log.Println("Added migrated token", event.Mint, "to addresses")
			}
		}
	}
}

// addMigratedTokenToAddresses reads the current addresses file and appends the new token if not already present.
func addMigratedTokenToAddresses(event MigrationEvent) error {
	addressesFile := filepath.Join("..", "addresses", "address.json")
	var addresses []Address
	if data, err := ioutil.ReadFile(addressesFile); err == nil {
		json.Unmarshal(data, &addresses)
	} else {
		addresses = []Address{}
	}
	if int64(len(addresses)) < 25 {
		// Check for duplicates.
		for _, addr := range addresses {
			if strings.EqualFold(addr.Address, event.Mint) {
				log.Println("Token already exists in addresses:", event.Mint)
				return nil
			}
		}

		// Fetch token metadata.
		metadata, err := getTokenMetadataFromAPI(event.Mint)
		if err != nil {
			log.Println("Failed to get metadata for", event.Mint, err)
			metadata = &TokenMetadata{Name: "", Symbol: "", URI: ""}
		}

		newIndex := int64(len(addresses))
		if newIndex > 4 {
			newIndex = int64(len(addresses)) % 5
		}
		// Record addition time
		now := time.Now().UTC()
		fmt.Println("Time Now", now)
		newAddress := Address{
			Address:      event.Mint,
			Name:         metadata.Name,
			Symbol:       metadata.Symbol,
			Index:        newIndex,
			AddedAt:      &now,
			LastActivity: &now,
		}
		addresses = append(addresses, newAddress)
		os.MkdirAll(filepath.Dir(addressesFile), os.ModePerm)
		file, err := os.Create(addressesFile)
		if err != nil {
			return err
		}
		defer file.Close()
		encoder := json.NewEncoder(file)
		encoder.SetIndent("", "  ")

		return encoder.Encode(addresses)
	}
	return nil
}

// ----------------------
// New Endpoints: Add Proxies, Cookies, and Addresses
// ----------------------

func addProxiesHandler(w http.ResponseWriter, r *http.Request) {
	outputPath := filepath.Join("..", "datacenter", "proxies.json")
	var proxies []Proxy
	if data, err := ioutil.ReadFile(outputPath); err == nil {
		json.Unmarshal(data, &proxies)
	}

	if r.Method == http.MethodPost {
		if err := r.ParseForm(); err != nil {
			http.Error(w, "Failed to parse form", http.StatusBadRequest)
			return
		}
		action := r.FormValue("action")
		switch action {
		case "add":
			newProxiesText := r.FormValue("newProxies")
			lines := strings.Split(newProxiesText, "\n")
			for _, line := range lines {
				line = strings.TrimSpace(line)
				if line != "" {
					proxies = append(proxies, Proxy{Address: line})
				}
			}
		case "edit":
			indexStr := r.FormValue("index")
			newProxyValue := strings.TrimSpace(r.FormValue("proxy"))
			index, err := strconv.Atoi(indexStr)
			if err == nil && index >= 0 && index < len(proxies) && newProxyValue != "" {
				proxies[index].Address = newProxyValue
			}
		case "delete":
			indexStr := r.FormValue("index")
			index, err := strconv.Atoi(indexStr)
			if err == nil && index >= 0 && index < len(proxies) {
				proxies = append(proxies[:index], proxies[index+1:]...)
			}
		default:
			http.Error(w, "Unknown action", http.StatusBadRequest)
			return
		}
		os.MkdirAll(filepath.Dir(outputPath), os.ModePerm)
		file, err := os.Create(outputPath)
		if err != nil {
			http.Error(w, "Error updating file", http.StatusInternalServerError)
			return
		}
		defer file.Close()
		encoder := json.NewEncoder(file)
		encoder.SetIndent("", "  ")
		if err := encoder.Encode(proxies); err != nil {
			http.Error(w, "Error encoding JSON", http.StatusInternalServerError)
			return
		}
		http.Redirect(w, r, "/add-proxies", http.StatusSeeOther)
		return
	}

	w.Header().Set("Content-Type", "text/html")
	html := `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Manage Proxies</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    textarea { width: 100%; height: 100px; }
    input[type="text"] { width: 300px; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
    th { background-color: #f2f2f2; }
    form.inline { display: inline; }
    button { margin: 2px; }
  </style>
</head>
<body>
  <h1>Manage Proxies</h1>
  <h2>Add New Proxies</h2>
  <form method="POST" action="/add-proxies">
    <textarea name="newProxies" placeholder="Enter proxies, one per line (IP:PORT)"></textarea><br>
    <input type="hidden" name="action" value="add">
    <button type="submit">Add Proxies</button>
  </form>
  <h2>Current Proxies</h2>
  <table>
    <tr>
      <th>#</th>
      <th>Proxy</th>
      <th>Actions</th>
    </tr>`
	for i, proxy := range proxies {
		html += `<tr>
      <td>` + fmt.Sprintf("%d", i) + `</td>
      <td>
        <form method="POST" action="/add-proxies" class="inline">
          <input type="text" name="proxy" value="` + proxy.Address + `" required>
          <input type="hidden" name="index" value="` + fmt.Sprintf("%d", i) + `">
          <input type="hidden" name="action" value="edit">
          <button type="submit">Update</button>
        </form>
      </td>
      <td>
        <form method="POST" action="/add-proxies" class="inline">
          <input type="hidden" name="index" value="` + fmt.Sprintf("%d", i) + `">
          <input type="hidden" name="action" value="delete">
          <button type="submit" onclick="return confirm('Delete proxy?');">Delete</button>
        </form>
      </td>
    </tr>`
	}
	html += `</table>
</body>
</html>`
	w.Write([]byte(html))
}

func addCookiesHandler(w http.ResponseWriter, r *http.Request) {
	outputPath := filepath.Join("..", "datacenter", "cookies.json")
	var cookies []Cookies

	// Load existing cookies.json if present
	if data, err := ioutil.ReadFile(outputPath); err == nil {
		err = json.Unmarshal(data, &cookies)
		if err != nil {
			http.Error(w, "Error parsing existing JSON", http.StatusInternalServerError)
			return
		}
	}

	if r.Method == http.MethodPost {
		// Parse form values
		if err := r.ParseForm(); err != nil {
			http.Error(w, "Failed to parse form", http.StatusBadRequest)
			return
		}

		action := r.FormValue("action")
		switch action {
		case "add", "edit":
			// Update both fields
			authToken := strings.TrimSpace(r.FormValue("auth_token"))
			ct0 := strings.TrimSpace(r.FormValue("ct0"))
			if authToken == "" || ct0 == "" {
				http.Error(w, "Both auth_token and ct0 are required", http.StatusBadRequest)
				return
			}
			cookies = append(cookies, Cookies{AuthToken: authToken, Ct0: ct0})

		case "delete":
			// Clear the JSON
			indexStr := r.FormValue("index")
			index, err := strconv.Atoi(indexStr)
			if err == nil && index >= 0 && index < len(cookies) {
				cookies = append(cookies[:index], cookies[index+1:]...)
			}
			//cookies = []Cookies{}

		default:
			http.Error(w, "Unknown action", http.StatusBadRequest)
			return
		}

		// Ensure directory exists
		os.MkdirAll(filepath.Dir(outputPath), os.ModePerm)

		// Write updated JSON
		file, err := os.Create(outputPath)
		if err != nil {
			http.Error(w, "Error updating file", http.StatusInternalServerError)
			return
		}
		defer file.Close()

		encoder := json.NewEncoder(file)
		encoder.SetIndent("", "  ")
		if err := encoder.Encode(cookies); err != nil {
			http.Error(w, "Error encoding JSON", http.StatusInternalServerError)
			return
		}

		// Redirect to refresh page
		http.Redirect(w, r, "/add-cookies", http.StatusSeeOther)
		return
	}

	// Render the HTML form
	// Render HTML form
	w.Header().Set("Content-Type", "text/html")
	html := `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Manage Cookies Array</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    input[type="text"] { width: 400px; margin-bottom: 10px; }
    button { margin-top: 10px; padding: 6px 12px; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { border: 1px solid #ccc; padding: 8px; }
    th { background-color: #f2f2f2; }
    form.inline { display: inline; }
  </style>
</head>
<body>
  <h1>Manage Cookies Array JSON</h1>

  <h2>Add New Entry</h2>
  <form method="POST" action="/add-cookies">
    <div>
      <label for="auth_token">Auth Token:</label><br>
      <input type="text" id="auth_token" name="auth_token" required>
    </div>
    <div>
      <label for="ct0">ct0:</label><br>
      <input type="text" id="ct0" name="ct0" required>
    </div>
    <input type="hidden" name="action" value="add">
    <button type="submit">Add</button>
  </form>

  <h2>Current Entries</h2>
  <table>
    <tr><th>#</th><th>Auth Token</th><th>ct0</th><th>Actions</th></tr>` + renderRows(cookies) + `
  </table>
</body>
</html>`

	w.Write([]byte(html))
}
func renderRows(cookies []Cookies) string {
	rows := ""
	for i, c := range cookies {
		rows += fmt.Sprintf(`
<tr>
  <td>%d</td>
  <td><form method="POST" action="/add-cookies" class="inline">
      <input type="text" name="auth_token" value="%s" required>
      <input type="text" name="ct0" value="%s" required>
      <input type="hidden" name="index" value="%d">
      <input type="hidden" name="action" value="edit">
      <button type="submit">Update</button>
    </form></td>
  <td>
    <form method="POST" action="/add-cookies" class="inline">
      <input type="hidden" name="index" value="%d">
      <input type="hidden" name="action" value="delete">
      <button type="submit" onclick="return confirm('Delete this entry?');">Delete</button>
    </form>
  </td>
</tr>`, i, c.AuthToken, c.Ct0, i, i)
	}
	return rows
}

// Helper to preview JSON in the page
func toJSONPreview(c Cookies) string {
	data, _ := json.MarshalIndent(c, "", "  ")
	return string(data)
}

func addAddressHandler(w http.ResponseWriter, r *http.Request) {
	outputPath := filepath.Join("..", "addresses", "address.json")
	var addresses []Address
	if data, err := ioutil.ReadFile(outputPath); err == nil {
		json.Unmarshal(data, &addresses)
	}

	if r.Method == http.MethodPost {
		if err := r.ParseForm(); err != nil {
			http.Error(w, "Failed to parse form", http.StatusBadRequest)
			return
		}
		action := r.FormValue("action")
		switch action {
		case "add":
			newAddress := strings.TrimSpace(r.FormValue("newAddress"))
			newIndexStr := strings.TrimSpace(r.FormValue("newIndex"))
			if newAddress != "" && newIndexStr != "" {
				newIndex, err := strconv.ParseInt(newIndexStr, 10, 64)
				if err == nil {
					metadata, err := getTokenMetadataFromAPI(newAddress)
					if err != nil {
						metadata = &TokenMetadata{Name: "", Symbol: "", URI: ""}
					}
					now := time.Now().UTC()
					addresses = append(addresses, Address{
						Address:      newAddress,
						Name:         metadata.Name,
						Symbol:       metadata.Symbol,
						Index:        newIndex,
						AddedAt:      &now,
						LastActivity: &now,
					})
				}
			}
		case "edit":
			rowStr := r.FormValue("row")
			newAddress := strings.TrimSpace(r.FormValue("address"))
			newIndexStr := strings.TrimSpace(r.FormValue("index"))
			row, err := strconv.Atoi(rowStr)
			if err == nil && row >= 0 && row < len(addresses) && newAddress != "" && newIndexStr != "" {
				newIndex, err2 := strconv.ParseInt(newIndexStr, 10, 64)
				if err2 == nil {
					metadata, err3 := getTokenMetadataFromAPI(newAddress)
					if err3 != nil {
						metadata = &TokenMetadata{Name: "", Symbol: "", URI: ""}
					}
					now := time.Now().UTC()
					addresses[row] = Address{
						Address:      newAddress,
						Name:         metadata.Name,
						Symbol:       metadata.Symbol,
						Index:        newIndex,
						AddedAt:      &now,
						LastActivity: &now,
					}
				}
			}
		case "delete":
			rowStr := r.FormValue("row")
			row, err := strconv.Atoi(rowStr)
			if err == nil && row >= 0 && row < len(addresses) {
				addresses = append(addresses[:row], addresses[row+1:]...)
			}
		default:
			http.Error(w, "Unknown action", http.StatusBadRequest)
			return
		}
		os.MkdirAll(filepath.Dir(outputPath), os.ModePerm)
		file, err := os.Create(outputPath)
		if err != nil {
			http.Error(w, "Error updating file", http.StatusInternalServerError)
			return
		}
		defer file.Close()
		encoder := json.NewEncoder(file)
		encoder.SetIndent("", "  ")
		if err := encoder.Encode(addresses); err != nil {
			http.Error(w, "Error encoding JSON", http.StatusInternalServerError)
			return
		}
		http.Redirect(w, r, "/add-address", http.StatusSeeOther)
		return
	}

	w.Header().Set("Content-Type", "text/html")
	html := `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Manage Addresses</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    input[type="text"], input[type="number"] { width: 200px; padding: 5px; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
    th { background-color: #f2f2f2; }
    form.inline { display: inline; }
    button { margin: 2px; }
  </style>
</head>
<body>
  <h1>Manage Addresses</h1>
  <h2>Add New Address</h2>
  <form method="POST" action="/add-address">
    <label for="newAddress">Mint Address:</label>
    <input type="text" name="newAddress" required>
    <label for="newIndex">Index:</label>
    <input type="number" name="newIndex" required>
    <input type="hidden" name="action" value="add">
    <button type="submit">Add Address</button>
  </form>
  <h2>Current Addresses</h2>
  <table>
    <tr>
      <th>#</th>
      <th>Mint Address</th>
      <th>Name</th>
      <th>Symbol</th>
      <th>Index</th>
      <th>Actions</th>
    </tr>`
	for i, addr := range addresses {
		html += fmt.Sprintf(`<tr>
      <td>%d</td>
      <td>
        <form method="POST" action="/add-address" class="inline">
          <input type="text" name="address" value="%s" required>
      </td>
      <td>%s</td>
      <td>%s</td>
      <td>
          <input type="number" name="index" value="%d" required>
          <input type="hidden" name="row" value="%d">
          <input type="hidden" name="action" value="edit">
          <button type="submit">Update</button>
        </form>
      </td>
      <td>
        <form method="POST" action="/add-address" class="inline">
          <input type="hidden" name="row" value="%d">
          <input type="hidden" name="action" value="delete">
          <button type="submit" onclick="return confirm('Delete this address?');">Delete</button>
        </form>
      </td>
    </tr>`, i, addr.Address, addr.Name, addr.Symbol, addr.Index, i, i)
	}
	html += `</table>
</body>
</html>`
	w.Write([]byte(html))
}

type SessionStatus struct {
	ID      int    `json:"id"`
	Port    int    `json:"port"`
	Status  string `json:"status"`
	Updated string `json:"updated"`
}

func readSessionStatusHandler(w http.ResponseWriter, r *http.Request) {
	filePath := "../datacenter/activesession.json"
	var sessions []SessionStatus
	if _, err := os.Stat(filePath); err == nil {
		data, err := ioutil.ReadFile(filePath)
		if err != nil {
			http.Error(w, "Error reading session file", http.StatusInternalServerError)
			return
		}
		if err := json.Unmarshal(data, &sessions); err != nil {
			http.Error(w, "Error parsing session data", http.StatusInternalServerError)
			return
		}
	} else {
		sessions = []SessionStatus{}
	}

	w.Header().Set("Content-Type", "text/html")
	html := `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Active Sessions</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; }
    th { background-color: #f2f2f2; }
  </style>
</head>
<body>
  <h1>Active Sessions</h1>`
	if len(sessions) == 0 {
		html += `<p>No active sessions found.</p>`
	} else {
		html += `<table>
      <tr>
        <th>ID</th>
        <th>Port</th>
        <th>Status</th>
        <th>Updated</th>
      </tr>`
		for _, s := range sessions {
			html += fmt.Sprintf(`<tr>
        <td>%d</td>
        <td>%d</td>
        <td>%s</td>
        <td>%s</td>
      </tr>`, s.ID, s.Port, s.Status, s.Updated)
		}
		html += `</table>`
	}
	html += `
</body>
</html>`
	w.Write([]byte(html))
}

// ----------------------
// Middleware Helpers
// ----------------------

func setHeaders(w http.ResponseWriter) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set(contentTypeKey, contentTypeVal)
}

func withCORS(handler http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}
		handler(w, r)
	}
}
func withCORSStat(handler http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}
		handler.ServeHTTP(w, r)
	})
}
func fetchHoldersForAllAddresses() {
	// path to your addresses file
	addrFile := filepath.Join("..", "addresses", "address.json")

	// read and parse
	data, err := ioutil.ReadFile(addrFile)
	if err != nil {
		log.Printf("[holders] could not read addresses file: %v", err)
		return
	}
	var addrs []Address
	if err := json.Unmarshal(data, &addrs); err != nil {
		log.Printf("[holders] invalid JSON in addresses file: %v", err)
		return
	}

	client := holders.NewBitqueryClient()
	for _, a := range addrs {
		// get transfers for this mint/address
		fmt.Println("Getting Holder Info for", a.Address)
		output, err := client.UpdateAndGetTransfers(a.Address)
		if err != nil {
			log.Printf("[holders] error fetching for %s: %v", a.Address, err)
			continue
		}
		// optionally unmarshal and log length
		var transfers []Transfer
		if err := json.Unmarshal([]byte(output), &transfers); err != nil {
			log.Printf("[holders] invalid JSON for %s: %v", a.Address, err)
		} else {
			log.Printf("[holders] fetched %d transfers for %s", len(transfers), a.Address)
		}

		// store raw JSON in Redis under "holders:<mint>"
		/*
			key := fmt.Sprintf("holders:%s", a.Address)
			if err := RedisClient.Set(key, output, 0).Err(); err != nil {
				log.Printf("[holders] failed to save to Redis for %s: %v", a.Address, err)
			}*/
		time.Sleep(3 * time.Second)
	}
}

// periodicHolderUpdater starts a ticker that fires every interval d.
func periodicHolderUpdater(d time.Duration) {
	fmt.Println("======== Getting Holders ============")
	ticker := time.NewTicker(d)
	defer ticker.Stop()

	// run once immediately
	fetchHoldersForAllAddresses()

	for range ticker.C {
		fetchHoldersForAllAddresses()
	}
}

type HolderSnapshot struct {
	Holders int    `json:"holders"`
	Time    string `json:"time"`
}

// getHolderSnapshotsHandler handles HTTP requests to retrieve holder snapshots.
func getHolderSnapshotsHandler(w http.ResponseWriter, r *http.Request) {
	// Extract the token address from query parameters
	tokenAddress := r.URL.Query().Get("address")
	if tokenAddress == "" {
		http.Error(w, "Missing 'address' query parameter", http.StatusBadRequest)
		return
	}

	// Construct the Redis key
	redisKey := fmt.Sprintf("token:%s:holdersplot", tokenAddress)

	// Retrieve the JSON array from Redis
	val, err := RedisClient.Get(redisKey).Result()
	if err == redis.Nil {
		http.Error(w, "No data found for the given address", http.StatusNotFound)
		return
	} else if err != nil {
		http.Error(w, fmt.Sprintf("Error retrieving data from Redis: %v", err), http.StatusInternalServerError)
		return
	}

	// Unmarshal the JSON array into a slice of HolderSnapshot
	var snapshots []HolderSnapshot
	if err := json.Unmarshal([]byte(val), &snapshots); err != nil {
		http.Error(w, fmt.Sprintf("Error unmarshaling JSON: %v", err), http.StatusInternalServerError)
		return
	}

	// Set response headers and write the JSON response
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(snapshots); err != nil {
		http.Error(w, fmt.Sprintf("Error encoding JSON response: %v", err), http.StatusInternalServerError)
		return
	}
}

// ----------------------
// Main Function
// ----------------------

func main() {
	// Start migration subscription in a goroutine.
	go subscribeMigration()
	go periodicHolderUpdater(2 * time.Minute)

	http.HandleFunc("/saveData", withCORS(saveDataHandler))
	http.HandleFunc("/tweet", withCORS(tweetHandler))
	http.HandleFunc("/tweeturl", withCORS(tweetURLHandler))
	http.HandleFunc("/api/token-metadata", withCORS(getTokenMetadata))
	http.HandleFunc("/fetch-data", withCORS(fetchKeysAndDataHandler))
	http.HandleFunc("/fetch-holders", withCORS(fetchHolderKeysAndDataHandler))
	http.HandleFunc("/add-proxies", withCORS(addProxiesHandler))
	http.HandleFunc("/add-cookies", withCORS(addCookiesHandler))
	http.HandleFunc("/add-address", withCORS(addAddressHandler))
	http.HandleFunc("/session-status", withCORS(readSessionStatusHandler))
	http.HandleFunc("/api/holder-snapshots", withCORS(getHolderSnapshotsHandler))

	staticPath := filepath.Join("..", "static")
	fs := http.FileServer(http.Dir(staticPath))
	http.Handle("/static/", withCORSStat(http.StripPrefix("/static/", fs)))

	addressPath := filepath.Join("..", "addresses")
	fs_addr := http.FileServer(http.Dir(addressPath))
	http.Handle("/addresses/", withCORSStat(http.StripPrefix("/addresses/", fs_addr)))

	slptokenPath := filepath.Join("..", "spltoken")
	fs_slptkn := http.FileServer(http.Dir(slptokenPath))
	http.Handle("/spltoken/", withCORSStat(http.StripPrefix("/spltoken/", fs_slptkn)))

	fmt.Println("Server running on http://localhost:3300")
	http.ListenAndServe(":3300", nil)
}
