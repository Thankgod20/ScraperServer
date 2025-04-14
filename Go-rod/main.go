package main

import (
	"context"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/go-rod/rod/lib/proto"
)

func main() {
	// List of cookie values (update the cookie name if needed)
	cookies := []string{
		"9909e43f889d1cdd3e6aa1c8972c2f8670c4e7e7",
		"50a85d090c248e4a49b8c2cee308143cfbf11e86",
		"f023300083ad41a5bb85452c781f89b192656190",
		"bcfc3a823813d5b2a3b54931d81b922fe00f7568",
		"d8cc135b547e6653d09a230da33b6e143a353c8f",
		"a6a89792dce66712eeffe5aa730d837c52735c05",
		"44e0c6add34d27217a9b41e6f2974f9e97fd389d",
	}

	// List of proxies in "IP:port:username:password" format.
	// We will use the first N proxies for N cookies.
	proxies := []string{
		"104.238.8.201:6059:uyxzmtjn:ietipyjz5ls7",
		"140.233.166.176:7209:uyxzmtjn:ietipyjz5ls7",
		"155.254.38.68:5744:uyxzmtjn:ietipyjz5ls7",
		"38.225.3.96:5379:uyxzmtjn:ietipyjz5ls7",
		"46.203.218.149:8803:uyxzmtjn:ietipyjz5ls7",
		"82.22.210.215:8057:uyxzmtjn:ietipyjz5ls7",
		"104.239.105.165:6695:uyxzmtjn:ietipyjz5ls7",
		// ... you can include additional proxies as needed.
	}

	var wg sync.WaitGroup

	// For each cookie, launch an instance using a corresponding proxy.
	for i, cookieValue := range cookies {
		// Make sure we have a proxy for each account.
		if i >= len(proxies) {
			log.Printf("Not enough proxies for cookie index %d", i)
			break
		}
		proxy := proxies[i]

		wg.Add(1)
		go func(instance int, cookieVal, proxyConf string) {
			defer wg.Done()

			// Build proxy URL in the format: http://username:password@IP:port
			var proxyURL string
			{
				var ip, port, user, pass string
				fmt.Println("proxyConf", proxyConf)
				_, err := fmt.Sscanf(proxyConf, "%s:%s:%s:%s", &ip, &port, &user, &pass)
				if err != nil {
					log.Printf("Instance %d: Failed to parse proxy string: %v", instance, err)
					return
				}
				proxyURL = fmt.Sprintf("http://%s:%s@%s:%s", user, pass, ip, port)
			}

			// Set up a launcher with the proxy.
			launch := launcher.New().
				Proxy(proxyURL).
				Headless(true)

			// Optionally, you can set a custom user data directory
			// or other flags if needed.
			url := launch.MustLaunch()

			// Connect to the browser using rod.
			browser := rod.New().ControlURL(url).MustConnect()
			defer browser.MustClose()

			// Create a new page and set a timeout context.
			_, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			page := browser.MustPage("")
			// Set the cookie for Twitter.
			// Adjust the cookie name and domain as required.
			err := page.SetCookies([]*proto.NetworkCookieParam{
				{
					Name:     "auth_token",
					Value:    cookieVal,
					Domain:   "x.com",
					Path:     "/",
					HTTPOnly: true,
					Secure:   true,
				},
			})
			if err != nil {
				log.Printf("Instance %d: Failed to set cookie: %v", instance, err)
				return
			}

			// Navigate to Twitter. Using the "home" page (or change to desired URL).

			err = page.Navigate("https://twitter.com/home")
			if err != nil {
				log.Printf("Instance %d: Navigation error: %v", instance, err)
				return
			}
			page.WaitLoad()
			//wait()

			// Give some time to ensure the page loads and the cookie is used.
			time.Sleep(5 * time.Second)

			log.Printf("Instance %d: Successfully loaded Twitter with provided cookie and proxy", instance)
		}(i, cookieValue, proxy)
	}

	wg.Wait()
}
