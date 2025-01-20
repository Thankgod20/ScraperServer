package main

import (
	"fmt"
	"math/rand"
	"strconv"
	"time"
)

// Base user agents
var baseUserAgents = []string{
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X {osVersion}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
	"Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:{version}) Gecko/20100101 Firefox/{version}",
	"Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{version}) Gecko/20100101 Firefox/{version}",
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/{version}",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X {osVersion}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
}

// Generate random user agents
func generateUserAgents(count int) []string {
	rand.Seed(time.Now().UnixNano())
	userAgents := make([]string, count)

	for i := 0; i < count; i++ {
		// Randomize version and OS details
		version := strconv.Itoa(rand.Intn(40) + 50)                           // Random browser version between 50 and 90
		osVersion := fmt.Sprintf("10_%d_%d", rand.Intn(15)+1, rand.Intn(5)+1) // Random macOS version
		baseAgent := baseUserAgents[rand.Intn(len(baseUserAgents))]

		// Replace placeholders
		userAgent := baseAgent
		userAgent = replacePlaceholder(userAgent, "{version}", version)
		userAgent = replacePlaceholder(userAgent, "{osVersion}", osVersion)

		userAgents[i] = userAgent
	}

	return userAgents
}

// Replace placeholder in the string
func replacePlaceholder(s, placeholder, value string) string {
	return string([]byte(s))
}

func main() {
	userAgents := generateUserAgents(100)

	// Print the user agents
	for i, ua := range userAgents {
		fmt.Printf("%d: %s\n", i+1, ua)
	}
}
