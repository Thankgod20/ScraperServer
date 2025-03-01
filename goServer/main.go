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
	"strings"
	"time"

	"github.com/blocto/solana-go-sdk/common"
	"github.com/blocto/solana-go-sdk/program/metaplex/token_metadata"
	"github.com/gagliardetto/solana-go"
	"github.com/gagliardetto/solana-go/rpc"
	"github.com/go-redis/redis"
	holders "github.com/thankgod20/scraperServer/Holders"
)

const (
	filePath_      = "extractedData.json"
	staticFolder   = "/static"
	tokenFolder    = "/spltoken"
	contentTypeKey = "Content-Type"
	contentTypeVal = "application/json"
)

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
}

var RedisClient *redis.Client

//var ctx = context.Background()

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

// Handler to fetch keys and data
func fetchHolderKeysAndDataHandler(w http.ResponseWriter, r *http.Request) {
	searchWord := r.URL.Query().Get("search") //"4x77NhFuVzWWDGEMUyB17e3nhvVdkV7HT2AZNmz6pump"
	pattern := fmt.Sprintf("%s", searchWord)
	fmt.Println("Partern", pattern)

	// Create a new BitqueryClient.
	client := holders.NewBitqueryClient()

	// Fetch transfer data, update the JSON file, and get its content.
	output, err := client.UpdateAndGetTransfers(pattern)
	if err != nil {
		log.Fatalf("Error updating and getting transfers: %v", err)
	}
	var transfers []Transfer
	if err := json.Unmarshal([]byte(output), &transfers); err != nil {
		log.Fatalf("Error decoding transfers: %v", err)
	}
	// If no values were found, return an appropriate response
	if len(transfers) == 0 {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"message": "No matching keys found"})
		return
	}

	// Send the response
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(transfers); err != nil {
		http.Error(w, fmt.Sprintf("Failed to encode response: %v", err), http.StatusInternalServerError)
	}
}

// Handler to fetch keys and data
func fetchKeysAndDataHandler(w http.ResponseWriter, r *http.Request) {
	searchWord := r.URL.Query().Get("search") //"4x77NhFuVzWWDGEMUyB17e3nhvVdkV7HT2AZNmz6pump"
	pattern := fmt.Sprintf("*spltoken:%s*", searchWord)

	// Get all keys matching the pattern
	keys, err := RedisClient.Keys(pattern).Result()
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to retrieve keys: %v", err), http.StatusInternalServerError)
		return
	}

	// Retrieve the values for each key
	var values []interface{}
	for _, key := range keys {
		value, err := RedisClient.Get(key).Result()
		if err != nil {
			log.Printf("Error retrieving value for key %s: %v", key, err)
			continue
		}
		var rawValue interface{}
		if err := json.Unmarshal([]byte(value), &rawValue); err == nil {
			// If JSON decoding succeeds, use the decoded value
			values = append(values, rawValue)
		} else {
			// Otherwise, use the raw value
			cleanedValue := strings.ReplaceAll(value, "\\\"", "\"")

			// Append the cleaned value
			values = append(values, cleanedValue)
		}

	}

	// If no values were found, return an appropriate response
	if len(values) == 0 {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"message": "No matching keys found"})
		return
	}

	// Send the response
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(values); err != nil {
		http.Error(w, fmt.Sprintf("Failed to encode response: %v", err), http.StatusInternalServerError)
	}
}
func main() {
	// Initialize server routes with CORS handling

	http.HandleFunc("/saveData", withCORS(saveDataHandler))
	http.HandleFunc("/tweet", withCORS(tweetHandler))
	http.HandleFunc("/tweeturl", withCORS(tweetURLHandler))
	http.HandleFunc("/api/token-metadata", withCORS(getTokenMetadata))

	http.HandleFunc("/fetch-data", withCORS(fetchKeysAndDataHandler))
	http.HandleFunc("/fetch-holders", withCORS(fetchHolderKeysAndDataHandler))

	staticPath := filepath.Join("..", "static")
	fs := http.FileServer(http.Dir(staticPath))
	http.Handle("/static/", withCORSStat(http.StripPrefix("/static/", fs)))

	//AddressPath
	addressPath := filepath.Join("..", "addresses")
	fs_addr := http.FileServer(http.Dir(addressPath))
	http.Handle("/addresses/", withCORSStat(http.StripPrefix("/addresses/", fs_addr)))
	//SplToken
	slptokenPath := filepath.Join("..", "spltoken")
	fs_slptkn := http.FileServer(http.Dir(slptokenPath))
	http.Handle("/spltoken/", withCORSStat(http.StripPrefix("/spltoken/", fs_slptkn)))
	// Start the server
	fmt.Println("Server running on http://localhost:3300")
	http.ListenAndServe(":3300", nil)
}

// Middleware to set headers
func setHeaders(w http.ResponseWriter) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set(contentTypeKey, contentTypeVal)
}

// withCORS adds CORS headers and handles preflight requests
func withCORS(handler http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		// Handle preflight request
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}

		// Call the original handler
		handler(w, r)
	}
}
func withCORSStat(handler http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Add CORS headers
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		// Handle preflight requests
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}

		// Serve the request
		handler.ServeHTTP(w, r)
	})
}
func getTokenMetadata(w http.ResponseWriter, r *http.Request) {
	// Extract the mint address from the URL query parameters
	mintAddress := r.URL.Query().Get("mint")
	if mintAddress == "" {
		http.Error(w, "Missing 'mint' query parameter", http.StatusBadRequest)
		return
	}

	// Convert the mint address string to a Solana PublicKey
	mintPubKey, err := solana.PublicKeyFromBase58(mintAddress)
	if err != nil {
		http.Error(w, fmt.Sprintf("Invalid mint address: %v", err), http.StatusBadRequest)
		return
	}

	// Initialize the Solana RPC client
	client := rpc.New(rpc.MainNetBeta_RPC)
	commonMintPubKey := common.PublicKeyFromBytes(mintPubKey.Bytes())
	// Derive the metadata account public key associated with the mint address
	metadataPubKey, err := token_metadata.GetTokenMetaPubkey(commonMintPubKey)
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to derive metadata public key: %v", err), http.StatusInternalServerError)
		return
	}

	// Fetch the metadata account information
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
	// Deserialize the metadata account data
	metadata, err := token_metadata.MetadataDeserialize(data)
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to deserialize metadata: %v", err), http.StatusInternalServerError)
		return
	}

	// Prepare the response with the desired metadata fields
	response := NFTMetadata{
		Name:   metadata.Data.Name,
		Symbol: metadata.Data.Symbol,
		URI:    metadata.Data.Uri,
	}

	// Set the response header and write the JSON response
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(response); err != nil {
		http.Error(w, fmt.Sprintf("Failed to encode response: %v", err), http.StatusInternalServerError)
	}
}

// saveDataHandler saves or updates token data
func saveDataHandler(w http.ResponseWriter, r *http.Request) {
	setHeaders(w)

	if r.Method != http.MethodPost {
		fmt.Println("Invalid request method")
		http.Error(w, "Invalid request method", http.StatusMethodNotAllowed)
		return
	}

	var newData map[string]map[string]interface{} //map[string]map[string][]string

	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		fmt.Println("Unable to read request body")
		http.Error(w, "Unable to read request body", http.StatusInternalServerError)
		return
	}
	defer r.Body.Close()
	fmt.Println("Raw body:", string(body))
	if err := json.Unmarshal(body, &newData); err != nil {
		fmt.Println("Invalid JSON")
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}
	rootDir := ensureFolder(staticFolder)
	existingData := make(map[string]map[string]interface{}) //make(map[string]map[string][]string)
	filePath := filepath.Join(rootDir, filePath_)
	// Load existing data
	if _, err := os.Stat(filePath); err == nil {
		data, err := ioutil.ReadFile(filePath)
		if err == nil {
			json.Unmarshal(data, &existingData)
		}
	}

	// Merge new data with existing data
	for tokenAddress, tokenData := range newData {
		fmt.Println("tokenAddress", tokenAddress, "tokenData", tokenData)
		if _, exists := existingData[tokenAddress]; !exists {
			existingData[tokenAddress] = tokenData
		}
	}

	// Write updated data to file
	data, _ := json.MarshalIndent(existingData, "", "  ")
	if err := ioutil.WriteFile(filePath, data, 0644); err != nil {
		http.Error(w, "Error saving data", http.StatusInternalServerError)
		return
	}

	w.Write([]byte("Data saved successfully"))
}

// tweetHandler processes incoming tweet data
func tweetHandler(w http.ResponseWriter, r *http.Request) {
	setHeaders(w)

	if r.Method != http.MethodPost {
		fmt.Println("Invalid request method")
		http.Error(w, "Invalid request method", http.StatusMethodNotAllowed)
		return
	}

	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		fmt.Println("Unable to read request body")
		http.Error(w, "Unable to read request body", http.StatusInternalServerError)
		return
	}
	defer r.Body.Close()
	fmt.Println("Recieveing", string(body))
	var requestData map[string]map[string]interface{}
	if err := json.Unmarshal(body, &requestData); err != nil {
		fmt.Println("Invalid JSON")
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
		// Construct and save the JSON in the desired format
		tweetContent, ok := data["tweet"].(string)
		if !ok || tweetContent == "" {
			fmt.Println("Missing or invalid tweet content")
			continue
		}

		if _, exists := fileData[tweetURL]; !exists {
			//fileData[tweetURL] = data["tweet"]
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

// tweetURLHandler processes tweet URL data
func tweetURLHandler(w http.ResponseWriter, r *http.Request) {
	setHeaders(w)

	if r.Method != http.MethodPost {
		fmt.Println("Invalid request method")
		http.Error(w, "Invalid request method", http.StatusMethodNotAllowed)

		return
	}

	body, err := ioutil.ReadAll(r.Body)
	if err != nil {
		fmt.Println("Unable to read request body")
		http.Error(w, "Unable to read request body", http.StatusInternalServerError)

		return
	}
	defer r.Body.Close()

	if strings.TrimSpace(string(body)) == "" {
		fmt.Println("Request body is empty")
		http.Error(w, "Request body is empty", http.StatusBadRequest)

		return
	}

	w.Write([]byte(`{"message": "Data saved successfully"}`))
}

// ensureFolder ensures the folder exists, creates if not
func ensureFolder(folder string) string {
	// Construct the path to the root directory relative to the current file
	rootDir := filepath.Join("..", folder) // ".." moves up one level from the current folder

	// Create the folder in the root directory
	if err := os.MkdirAll(rootDir, os.ModePerm); err != nil {
		fmt.Println(err)

	}

	//fmt.Println("Folder created at:", rootDir)
	/*
		if _, err := os.Stat(folder); os.IsNotExist(err) {
			os.MkdirAll(folder, 0755)
		}*/
	return rootDir
}

// contains checks if a slice contains a string
func contains(slice []string, item string) bool {
	for _, value := range slice {
		if value == item {
			return true
		}
	}
	return false
}
